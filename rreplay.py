from __future__ import print_function

import os
import sys
import subprocess
import re
import ConfigParser
import time


if __name__ == '__main__':
    cfg  = ConfigParser.SafeConfigParser()
    cfg.read(sys.argv[1])
    #os.environ['RR_LOG'] = 'ReplaySession'
    f = open('proc.out', 'w', 0)
    subjects = []
    for i in cfg.sections():
        subjects.append({'event': cfg.get(i, 'event'),
                         'trace_file': cfg.get(i, 'trace_file'),
                         'trace_start': cfg.get(i, 'trace_start'),
                         'trace_end': cfg.get(i, 'trace_end')})
    for i in subjects:
        # Add multiple event capability to rr
        command = ['rr', 'replay', '-a', '-n', i['event']]
        proc = subprocess.Popen(command, stdout=f, stderr=f)
    time.sleep(3)
    f.close()
    f = open('proc.out', 'r')
    lines = f.readlines()
    lines = [x.strip().split(' ') for x in lines if re.match('EVENT: [0-9]+ PID: [0-9]+', x)]
    for x in lines:
        for y in subjects:
            if y['event'] == x[1]:
                y['pid'] = x[3]
    handles = []
    for i in subjects:
        handles.append({'event': i['event'], 'handle': subprocess.Popen(['python',
                                                                         './inject.py',
                                                                         i['pid'],
                                                                         i['event'],
                                                                         i['trace_file'],
                                                                         i['trace_start'],
                                                                         i['trace_end']])})
    for h in handles:
        if h['handle'].wait() != 0:
            print('Injector for event {} failed'.format(h['event']))

