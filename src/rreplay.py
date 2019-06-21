#!/usr/bin/env python2
# pylint: disable=bad-indentation, unused-argument, invalid-name,
"""
<Program Name>
  rreplay.py

<Started>
  November 2017

<Author>
  Preston Moore
  Alan Cao

<Purpose>
  Performs a replay of a rrtest-formatted test, parsing the config.ini
  file and hooking onto the rrdump pipe for data from the modified rr
  process. This allows us to then call upon the injector, which compares
  the trace against the execution for divergences / deltas.

"""


from __future__ import print_function

import os
import signal
import sys
import subprocess
import ConfigParser
import json
import logging
import argparse

import consts
import syscallreplay.util as util

logger = logging.getLogger('root')

# pylint: disable=global-statement
rrdump_pipe = None


def get_message(pipe_name):
  """
  <Purpose>
    Opens a named pipe for communication between
    modified rr and rreplay. This allows for messages
    to be read and returned in a buffer for further processing.

  <Returns>
    buf: a list of messages collected from the named pipe

  """
  global rrdump_pipe

  # check if pipe path exists
  if not rrdump_pipe:
    while not os.path.exists(pipe_name):
      continue
    rrdump_pipe = open(pipe_name, 'r')

  # read message from pipe into buffer, and return
  buf = ''
  while True:
    buf += rrdump_pipe.read(1)
    if buf == '':
      return ''
    if buf[-1] == '\n':
      return buf
# pylint: enable=global-statement




def get_configuration(ini_path):
  """
  <Purpose>

    Parse the main configuration (config.ini) in order to figure out what
    events need process sets generated for them, what processes we are
    interested in at a given event, what mutator to apply to a given
    process+event combo, etc.

    The main config is generated by rrtest using the chosen mutators simulation
    opportunity identification opportunities.

    The contents of this config are broken down into subjects which are used
    both to create smaller "event configs" by
    create_event_configuration_files() and to track which tests have been
    completed in wait_for_handles().

    These subjects are sorted by event number so rr receives them in "event-
    chronological" order as we cannot ask rr to go backwards when we run it in
    unattended mode.


  <Returns>
    rr_dir: the directory in which the rr-generated trace files are stored
    subjects: a list of dicts that store parsed items from the config INI path

  """

  # instantiate new SafeConfigParser, read path to config
  logger.debug("Begin parsing INI configuration file")
  cfg = ConfigParser.SafeConfigParser()

  # discovering INI path
  logger.debug("Reading configuration file")
  found = cfg.read(ini_path)
  if ini_path not in found:
    raise IOError('INI configuration could not be read: {}'
                  .format(ini_path))

  # instantiate vars and parse config by retrieving sections
  subjects = []
  sections = cfg.sections()

  # set rr_dir as specified key-value pair in config, cut out first element
  # in list
  logger.debug("Discovering replay directory")
  rr_dir_section = sections[0]
  rr_dir = cfg.get(rr_dir_section, 'rr_dir')
  sections = sections[1:]

  if (len(sections) == 0):
    raise ConfigParser.NoSectionError('No simulation opportunities present in config.ini')

  # Each section in config.ini becomes a single test subject
  logger.debug("Parsing INI configuration")
  for i in sections:
    s = {}
    s['event'] = cfg.get(i, 'event')
    s['rec_pid'] = cfg.get(i, 'pid')
    s['trace_file'] = cfg.get(i, 'trace_file')
    s['trace_start'] = cfg.get(i, 'trace_start')
    s['trace_end'] = cfg.get(i, 'trace_end')
    s['mutator'] = cfg.get(i, 'mutator')
    # injected_state_file is the name we should give the event configuration
    # when we generate it later
    s['injected_state_file'] = s['event'] + '_' + s['mutator'] + '_state.json'

    # Other procs are uninteresting processes in the subject's associated
    # process set.  We can populate this list once we receive messages from
    # rr indicating which real process is the one we simulate on and which
    # ones we just need to remember to clean up.
    s['other_procs'] = []

    # mmap_backing_files is optional if we aren't using that feature
    try:
      s['mmap_backing_files'] = cfg.get(i, 'mmap_backing_files')
    except ConfigParser.NoOptionError:
      pass

    # checkers are also optional
    try:
      s['checker'] = cfg.get(i, 'checker')
    except ConfigParser.NoOptionError:
      pass

    # append subject to list
    logger.debug("Subject parsed: {}".format(s))
    subjects.append(s)

  # Sort the test subjects by event for the above reason
  def _sort_by_event(sub):
    return int(sub['event'])
  subjects.sort(key=_sort_by_event)

  # clean up any brk() kernel mapping files that might already exist
  # for each of the subjects

  for i in subjects:
    try:
      os.remove(s['rec_pid'] + '_brks.json')
    except OSError:
      pass

  return rr_dir, subjects





def create_event_configuration_files(subjects):
  """
  <Purpose>

  This function takes the test subjects parsed out of the main config file we
  are using and writes them out to individual event-specific config files.
  These event specific config files are picked up when rr indicates it has
  generated a process set for this event and used to create specific config
  files that tie together a test configuration process set metadata.

  <Returns>
  None


  """

  for s in subjects:
    with open(s['injected_state_file'], 'w') as d:
      json.dump(s, d)
      d.flush()





def execute_rr(rr_dir, subjects):
  """
  <Purpose>
  This method launches rr based on the requirements parsed out of the main
  configuration file (config.ini).

  <Returns>
    None

  """

  # create a new event string that tells rr which recorded pid:event combos
  # to generate process sets for.
  # Format  (e.g rec_pid:event)
  events_str = ''
  for i in subjects:
    events_str += i['rec_pid'] + ':' + i['event'] + ','
  logger.debug("Event string: {}".format(events_str))

  # retrieve environmental variables (mostly for running rr with the user's
  # configured RR_LOG value)
  my_env = os.environ.copy()

  # Execute rr with spin-off switch.  Output tossed into proc.out
  # When launched in this fashion, rr will send messages out through
  # a pipe (rrdump_proc.pipe) whenever a process set is ready.
  # We monitor this pipe with process_messages and assign process sets
  # test subjects as rr generates them.
  logger.debug("Executing replay command and writing to proc.out")
  command = ['rr', 'replay', '-a', '-n', events_str, rr_dir]
  with open(consts.PROC_FILE, 'w') as f:
    subprocess.Popen(command, stdout=f, stderr=f, env=my_env)





def process_messages(subjects):
  """
  <Purpose>
    This is where the magic happens.  This function retrieves messages from rr
    via the named pipe we created earlier (rrdump_proc.pipe).  rr posts a set
    of these messages to the pipe every time it generates a process set.  These
    messages tell us to either INJECT or DONT_INJECT each process in a process
    set based on whether the process is on in which we will allow a mutator to
    simulate an anomaly.

    Processes that are marked as INJECT result in a copy of the crashsim process
    supervisor code in inject.py being launched.  From here, this code attaches
    to the process set in question and simulates an anomaly using the mutator
    assigned to the process set.

    The configuration that drives this processis derived from the event
    configuration described in create_event_configuration_files().  This
    more specific configuration maps the details in the event configuration
    to a real process set so the process supervisor (inject.py) knows to
    what it should attach.

    Processes that are marked as DONT_INJECT are added to the test subject
    associated with the process set's "other processes" list so we can keep
    track of them and kill them off after testing has finished.


    A message on the pipe looks like:
    INJECT: EVENT: <event number> PID: <pid> REC_PID: <rec_pid>\n
    or
    DONT_INJECT: EVENT: <event number> PID: <pid> REC_PID: <rec_pid>\n

  <Returns>
    None

  """


  # we want to loop until all of our test subjects have been associated
  # with a process set from rr.  As a result, we must track how many subjects
  # we have handled.  Because we will get multiple messages per process set,
  # this loop will run many times per subject (once or each process in its process set)
  subjects_handled = set()
  while len(subjects_handled) < len(subjects):
    message = get_message(consts.RR_PIPE)
    logger.debug("Parsing retrieved message: {}".format(message))
    parts = message.split(' ')
    inject = parts[0].strip()[:-1]
    event = parts[2]
    pid = parts[4]

    # Get all the subjects we can apply at a given event
    subjects_for_event = []
    for i in subjects:
      # From our total list of subjects, we want to get only ones whose
      # event corresponds to what rr has just generated (i.e. its events == event)
      # and only if we have not already assigned it to a process set
      if i['event'] == event and (i['event'], i['mutator']) not in subjects_handled:
        subjects_for_event.append(i)

    # rr has reported that it generated a process for us as part
    # of creating a process set.  We want to wait to confirm this process
    # is alive before we continue.
    logger.debug("Checking if process {} is alive".format(pid))
    while not util.process_is_alive(pid):
      print('waiting...')

    # checking inject state
    if inject == 'INJECT':
      logger.debug("PID {} is being injected".format(pid))

      # Here we generate specific config file for a subject+process set pair.
      try:
        # First we grab the next subject that can simulate an anomaly at this
        # rr event
        s = subjects_for_event.pop()
      except IndexError:
        logger.warning('rr generated an extra process for which we have no subject')
        logger.warning('We will ignore it but the zombie will remain running')
        continue
      # mark the subject as handled so we don't grab it again above
      subjects_handled.add((s['event'], s['mutator']))
      # Load the event-level config for the subject we're working on
      eventwise_statefile = s['injected_state_file']
      with open(eventwise_statefile, 'r') as d:
        tmp = json.load(d)
        # Add a pid field to the dict we loaded from the event-level config
        tmp['pid'] = pid
        # Generate a unique name for new subject+pid specific config
        pid_unique_statefile = tmp['pid'] + '_' + eventwise_statefile
      with open(pid_unique_statefile, 'w') as d:
        json.dump(tmp, d)
        d.flush()
      logger.debug('Generated subject+pid specific config named {}'
                   .format(pid_unique_statefile))
      # Kick off the process supervisor supplying it with the subject+pid
      # specific config we just generated we just generated.
      s['handle'] = subprocess.Popen(['inject',
                                      '--verbosity=40',
                                      pid_unique_statefile])

    # Otherwise, we just ask the subject to track these uninteresting processes
    # so we can clean them up after testing is done.
    elif inject == 'DONT_INJECT':
      logger.debug("PID {} is not being injected".format(pid))
      s['other_procs'].append(pid)






def wait_on_handles(subjects):
  """
  <Purpose>

  This function waits on the supervisor processes (inject.py) to complete
  their tests before cleaning everything up.

  <Returns>
    None

  """

  for s in subjects:
    # If we have a handle for a given subject (i.e. there wasn't some
    # earlier problem getting the subject matched up with a process set),
    # we wait for its supervisor process to finish its testing here.
    if 'handle' in s:
      logger.debug("waiting on {}".format(s))
      ret = s['handle'].wait()
    else:
      logger.error('No handle associated with subject {}'.format(s))
      ret = -1

    #  If a subject has any uninteresting "other processes" associated with it,
    #  we kill them here.
    for i in s['other_procs']:
      logger.debug("{} to be killed.".format(s['other_procs'][i]))
      try:
        os.kill(int(i), signal.SIGKILL)
      except OSError:
        pass

    # Report to the user if a supervisor failed unexpectedly
    if ret != 0:
      logger.error('CrashSimulator supervisor for event:rec_pid {}:{} failed'
                   .format(s['event'], s['rec_pid']))





def cleanup():
  """
  <Purpose>
    Delete generated output file and pipe if necessary.

    CrashSimulator generates several files when performing execution,
    storing output and providing a means for IPC with modified rr.

  <Returns>
    None

  """

  logger.debug("Cleaning up")
  if os.path.exists(consts.PROC_FILE):
    logger.debug("Deleting proc.out")
    os.unlink(consts.PROC_FILE)
  if os.path.exists(consts.RR_PIPE):
    logger.debug("Deleting rrdump_proc.pipe")
    os.unlink(consts.RR_PIPE)



def call_replay(test_name, verbosity):
  # ensure that a pre-existing pipe is unlinked before execution
  cleanup()

  # check if user-specified test exists
  test_dir = consts.DEFAULT_CONFIG_PATH + test_name
  if not os.path.exists(test_dir):
    print("Test {} does not exist. Create before attempting to configure!" \
            .format(test_name))
    sys.exit(1)

  # read config.ini from the test directory
  rr_dir, subjects = get_configuration(test_dir + "/" + "config.ini")

  create_event_configuration_files(subjects)

  # execute rr
  execute_rr(rr_dir, subjects)

  # process pipe messages
  process_messages(subjects)

  # wait on handles
  wait_on_handles(subjects)

  # cleanup routine
  cleanup()





def main():
  # initialize argparse
  parser = argparse.ArgumentParser()
  parser.add_argument('-v', '--verbosity',
                      dest='loglevel',
                      action='store_const',
                      const=logging.DEBUG,
                      help='flag for displaying debug information')
  parser.add_argument('testname',
                      help='specify rrtest-created test for replay')

  # parse arguments
  args = parser.parse_args()

  call_replay(args.testname, args.loglevel)




if __name__ == '__main__':
  try:
    main()
    sys.exit(0)

  # if there is some sort of hanging behavior, we can cleanup if user sends a
  # SIGINT
  except KeyboardInterrupt:
    logging.debug("Killing rreplay.\nDumping proc.out")

    # read output
    with open('proc.out', 'r') as content_file:
      print(content_file.read())

    # ensure clean exit by unlinking
    cleanup()
    sys.exit(0)

  # catch any other sort of exception that may occur, and ensure proper cleanup
  # is still performed
  except Exception as e:
    cleanup()
    raise e
