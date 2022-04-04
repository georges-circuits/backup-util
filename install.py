from app import app_path
from os import getuid
from os.path import join, isfile
from getpass import getuser
from subprocess import run
import sys, time

if getuid() != 0:
    user = getuser()
    print(f'started under "{user}" restarting as root using sudo')
    run(['sudo', 'python3', sys.argv[0], user])
    exit()


service_file_name = 'backup-utility.service'
default_service_dir_path = '/etc/systemd/system/'
default_user = sys.argv[1] if len(sys.argv) >= 2 else getuser()
xauthority_file_path = join('/home/', default_user, '.Xauthority')


def prompt(message, default = ''):
    ret = input(f'{message} (default is "{default}"): ').strip()
    if not ret:
        ret = default
    return ret


user = prompt('user under which the service should run', default_user)
service_file_path = join(prompt('service directory', default_service_dir_path), service_file_name)
xauthority_file_path = prompt('.Xauthority', xauthority_file_path)

if not isfile(xauthority_file_path):
    print(f'{xauthority_file_path} is not a file')
    exit()


service_file = \
f"""[Unit]
Description=backupUtil
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
Restart=on-failure
User={user}
ExecStart=/bin/bash -c "export DISPLAY=:0; export XAUTHORITY={xauthority_file_path}; /usr/bin/python3 {app_path}"

[Install]
WantedBy=graphical.target
"""

print('service file')
print('\n'.join([f">  {l}" for l in service_file.split('\n')]))
print(f'will be written to "{service_file_path}"')

if input('enter "y" to continue ').lower().strip() != 'y':
    print('canceling')
    exit()

with open(service_file_path, 'w') as file:
    file.write(service_file)

run(['systemctl', 'daemon-reload'])
run(['systemctl', 'enable', service_file_name])
run(['systemctl', 'start', service_file_name])

print('\nthe window should appear now, print service status in 3 seconds:')

time.sleep(3)
run(['systemctl', 'status', service_file_name])


