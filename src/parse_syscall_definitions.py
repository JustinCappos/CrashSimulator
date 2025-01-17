"""
<Program Name>
  parse_syscall_definitions

<Started>
  June 2013

<Author>
  Savvas Savvides <savvas@purdue.edu>
  Alan Cao <ac7758@nyu.edu>

<Purpose>
  Parse the definitions of all system calls from their man pages.

  First retrieve system calls names using a one-line comand through subprocess.
  Then for each system call read its man page and get its definition.


  Manual pages (man) are read using the subprocess library.


  Example running this program:
    run:
      python parse_syscall_definitions.py

    - several different views are provided. read the main method at the end of
    this file and uncomment appropriately.

    - the option of saving the system call definitions to a pickle file is also
    provided.

"""

import re
import signal
import subprocess
import pickle

import consts
from sysDef.SyscallManual import SyscallManual
from sysDef.SyscallManual import SyscallManualException


def parse_syscall_names_list():
    """
    <Purpose>
      Uses a command to retrieve output of all system call names in the system

    <Arguments>
      None

    <Exceptions>
      None

    <Side Effects>
      None

    <Returns>
      syscall_names_list:
        A list of all the system call names gathered from syscall retrieval command
    """

    # create command for retrieving section 2 system calls efficiently
    ls_man_command = "ls /usr/share/man/man2 | sed -e s/.2.gz//g | xargs man -s 2 -k  | sort"

    # command is passed as string, in order for pipe to carry out
    ls_man_process = subprocess.Popen(ls_man_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    ls_man_output = ls_man_process.communicate()[0]

    # split output into list of lines for each system call
    man_lines = ls_man_output.split("\n")

    syscall_names_list = []

    # remove description from each line, retrieve only the system call name, and
    # append to list
    for line in man_lines:
        syscallname = line.split(' ', 1)[0].strip()
        syscall_names_list.append(syscallname)

    # check if last element is empty
    if syscall_names_list[-1] == '':
        syscall_names_list.pop()

    return syscall_names_list



def get_syscall_definitions_list(syscall_names_list):
    """
    <Purpose>
      Given a list of syscall names, it returns a list of SyscallManual objects.

    <Arguments>
      syscall_names_list:
        a list of system call names.

    <Exceptions>
      None

    <Side Effects>
      None

    <Returns>
      syscall_definitions_list:
        A list of SyscallManual objects.

    """
    syscall_definitions_list = []

    # when a SyscallManual definition returns an exception, the system call is a special case,
    # as it not unimplemented nor unavailable. It is therefore skipped and not listed.
    for syscall_name in syscall_names_list:
        try:
            syscall_definitions_list.append(SyscallManual(syscall_name))
        except SyscallManualException as e:
            print str(e) + " Skipping system call."
            continue

    return syscall_definitions_list





def print_definitions(syscall_definitions_list):
    """
    A view of the parsed definitions.
    - Lists the syscall names for which a definition was NOT found.
    - Prints the reason the definition was not found.
    - Lists the type of all syscalls i.e:
        - found
        - no manual page
        - not found in manual page
        - unimplemented system call.
    """

    print "List of all syscall names for which a definition was not found"
    print "=============================================================="
    for sd in syscall_definitions_list:
        if(sd.type != SyscallManual.FOUND):
            print sd.name

    print
    print

    # remember the type of each syscall to provide statistics at the end.
    found = []
    no_man = []
    not_found = []
    unimplemented = []

    print "Syscall names and the reason its definition was not found"
    print "========================================================="
    for sd in syscall_definitions_list:
        if(sd.type == SyscallManual.FOUND):
            found.append(sd.name)
            continue
        elif(sd.type == SyscallManual.NO_MAN_ENTRY):
            no_man.append(sd.name)
        elif(sd.type == SyscallManual.NOT_FOUND):
            not_found.append(sd.name)
        else:    # unimplemented
            unimplemented.append(sd.name)

        print sd
        print

    print

    print str(len(found)) + " syscall definitions found"
    print "-----------------------------"
    for name in found:
        print name

    print
    print

    print str(len(no_man)) + " syscall definitions with no manual entry"
    print "-------------------------------------------"
    for name in no_man:
        print name

    print

    print str(len(not_found)) + " definitions not found in their man entry"
    print "-------------------------------------------"
    for name in not_found:
        print name

    print

    print str(len(unimplemented)) + " system calls identified as unimplemented"
    print "-------------------------------------------"
    for name in unimplemented:
        print name

    print
    print





def pickle_syscall_definitions(syscall_definitions_list, target_dir):
    pickle_name = target_dir + "syscall_definitions.pickle"
    pickle_file = open(pickle_name, 'wb')
    pickle.dump(syscall_definitions_list, pickle_file)
    pickle_file.close()





def generate_pickle(target_dir=consts.DEFAULT_CONFIG_PATH):
    
    # get a list with all the system call names available in this system.
    syscall_names_list = parse_syscall_names_list()
    
    # output definitions
    #print_definitions(syscall_names_list)

    # use the list of names just parsed to generate a list of system call
    # definitions.
    syscall_definitions_list = get_syscall_definitions_list(syscall_names_list)

    # Alan - for CrashSimulator purposes, we will keep this quiet
    # pickle syscall_definitions_list
    pickle_syscall_definitions(syscall_definitions_list, target_dir)

