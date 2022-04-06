import tkinter as tk
from tkinter import messagebox
from configparser import ConfigParser
from threading import Thread
from os.path import realpath, join
from datetime import datetime, timedelta
from importlib.machinery import SourceFileLoader
import subprocess, time, logging


logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s', level=logging.DEBUG)
logger = logging.getLogger(__name__)

app_path = realpath(__file__)
base_path = app_path.strip('app.py')
local_file_path = join(base_path, 'local.py')
default_config_file_path = join(base_path, 'backup.conf')

class Checker:
    def can_backup(self):
        return True 

def sizeof_fmt(num, suffix="B"):
    for unit in ["", "Ki", "Mi", "Gi", "Ti", "Pi", "Ei", "Zi"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.1f}Yi{suffix}"


class rsyncProcess:
    """ 
    rsync subprocess class, it should be possible to wrap any other program 
    as long as the class adheres to the same interface

    required config["rsync"] entries are
    - "from" string specifying the path to backup (source)
    - "to" string specifying the path to backup to (destination)

    """

    def __init__(self, config: dict) -> None:
        # read parameters from config, assert required parameters
        assert 'rsync' in config, 'config needs to have an "rsync" section'
        self.config = config['rsync']
        assert 'from' in self.config, 'section "rsync" needs to include "from" path parameter'
        assert 'to' in self.config, 'section "rsync" needs to include "to" path parameter'
        self.path_from = self.config['from']
        self.path_to = self.config['to']
        self.options = self.config['options'] if 'options' in self.config else ''

        self.progress = 0
        self.size = 0
        self.speed = ''
        self.running = True
        self.exception = None
        self.thread = Thread(target=self._run)
        self.thread.start()

    def _get_cmd(self, options = ''):
        return f'rsync {self.options} {options} {self.path_from} {self.path_to}'.split(' ')

    def _run(self):
        try:
            self.progress = 0
            with subprocess.Popen(
                self._get_cmd('--info=progress2'), stdout=subprocess.PIPE, bufsize=1, text=True
            ) as process:
                for line in process.stdout:
                    if not self.running:
                        process.terminate()
                        raise
                    items = line.strip().split(' ')
                    info = tuple(filter(None, items))
                    try:
                        self.size = int(info[0].replace(',', ''))
                        self.progress = int(info[1].removesuffix('%')) / 100
                        self.speed = info[2]
                    except:
                        line = line.strip()
                        if line:
                            logger.warning(f'unexpected line from rsync: {line}')
        
        except Exception as e:
            if self.running:
                logger.error(f'rsync process exception: {e}')
                self.exception = e

        self.running = False

    def get_progress(self) -> float:
        return self.progress

    def get_size(self) -> int:
        return self.size

    def get_speed(self) -> str:
        return self.speed

    def is_running(self) -> bool:
        return self.running

    def was_successful(self) -> bool:
        return not self.exception

    def cancel(self):
        self.running = False
        self.thread.join()



class GUI(tk.Tk):
    def __init__(self, config_path: str, backup_process_class):
        super().__init__()
        self.config_path = config_path
        self.backup_process_class = backup_process_class

        logger.debug(f'reading config file {self.config_path}')
        self.config = ConfigParser()
        self.config.read(config_path)
        
        # write the default config section if not present
        if not 'backups' in self.config.sections():
            logger.warning(f'initializing config file with defaults for section "backups"')
            self.config.add_section('backups')
            self.config['backups']['delay'] = str(6.0)
            self.config['backups']['countdown'] = str(0.5)
            self.config['backups']['hide_after'] = str(0.25)
            self.save_config()

        self.backup_process = None
        self.last_backup_time = 0 #TODO the thing could have a log, which it would query 
        self.last_user_action_time = time.time()
        self.last_unhide_time = time.time()
        self.hidden = False
        self.last_backup_status = ''
        self.last_backup_start = 0
        self.can_backup_last_poll = 0
        self.can_backup_last = False

        next_at = int(self.config.get('backups', 'next_at', fallback='0'))
        if next_at < time.time() or next_at > time.time() + self.get_backup_period():
            self.schedule_next_backup(self.get_countdown_period())
        else:
            logger.info(f'found valid "next_at = {next_at}" in the config')
            self.schedule_next_backup(next_at - time.time() + 1)

        logger.debug(f'attempting to load {local_file_path} module')
        try:
            local = SourceFileLoader("local", local_file_path).load_module()
            self.checker = local.Checker()
        except Exception as e:
            logger.warning(f'failed to load {local_file_path}: {e}')
            self.checker = Checker()

        logger.debug(f'initializing GUI')

        self.protocol("WM_DELETE_WINDOW", self.close_handler)
        self.wm_iconphoto(True, tk.PhotoImage(file=join(base_path, f'media/icon.png')))
        self.set_title()

        self.status_frame = tk.Frame(self)
        self.init_status(self.status_frame, 2)
        self.status_frame.pack(padx=5, pady=2)

        self.buttons_frame = tk.Frame(self)
        self.init_buttons(self.buttons_frame)
        self.buttons_frame.pack(padx=5, pady=2)
        self.update_buttons()

        self.controller()

    def init_status(self, master, label_count):
        self.status = []
        for i in range(label_count):
            self.status.append(tk.Variable())
            tk.Label(master, textvariable=self.status[-1]).pack()

    def init_buttons(self, master):
        BUTTONS = [
            {'id': 'start',        'text': 'Backup now',     'command': self.start_backup_user},
            {'id': 'next_short',   'text': '1 hour later',   'command': lambda t=1: self.postpone_backup_user(t)},
            {'id': 'next_long',    'text': '1 day later',    'command': lambda t=24: self.postpone_backup_user(t)},
            {'id': 'cancel',       'text': 'Cancel',         'command': self.cancel_backup},
            {'id': 'hide',         'text': 'Hide',           'command': self.hide_user}
        ]
        self.buttons = {}
        for btn in BUTTONS:
            self.buttons[btn['id']] = tk.Button(master, text=btn['text'], command=btn['command'], width=10)
            self.buttons[btn['id']].pack(padx=2, pady=2, side=tk.LEFT)

    def set_title(self, message=''):
        if message:
            message = f'- {message}'
        self.title(f'backup util {message}')

    def is_in_countdown(self):
        return self.next_backup_time - self.get_countdown_period() < time.time()

    def visible_long_enough(self):
        return self.last_unhide_time + self.get_countdown_period() < time.time()

    def backup_is_running(self):
        return self.backup_process != None

    def postpone_backup_user(self, hours):
        self.last_user_action_time = time.time()
        self.schedule_next_backup(self.next_backup_time - time.time() + (hours * 3600))
        self.update_backup_status()
    
    def controller(self):
        if not self.hidden:
            self.update_status()

            ht = time.time() - self.get_hide_period()
            if self.last_backup_time < ht and self.last_user_action_time < ht and not self.backup_is_running() and not self.is_in_countdown():
                logger.debug('hiding because of user inactivity and sufficient time from last backup')
                self.hide()
        
        elif self.is_in_countdown() and self.can_backup():
            logger.debug('un-hiding, entering countdown state')
            self.unhide()

        if self.next_backup_time < time.time() and self.visible_long_enough() and not self.backup_is_running() and self.can_backup():
            self.start_backup()
        
        self.after(10000, self.controller)

    def start_backup_user(self):
        self.last_user_action_time = time.time()
        if self.can_backup():
            self.user_run = True
            self.start_backup()
        else:
            self.invalid_action('cannot backup at this time')

    def start_backup(self):
        if self.backup_is_running():
            self.invalid_action('backup is already in progress')
        else:
            logger.info(f'starting a backup')
            self.last_backup_start = time.time()
            self.backup_process = self.backup_process_class(self.config)
            self.update_buttons()
            self.monitor_backup()

    def monitor_backup(self):
        if self.backup_is_running():
            if not self.backup_process.is_running():
                
                self.last_backup_status = ''
                s = self.timedelta2string(datetime.now() - datetime.fromtimestamp(self.last_backup_start))                
                if self.backup_process.was_successful():
                    logger.info('backup was successful')
                    self.last_backup_status += f'took {s} and transferred {sizeof_fmt(self.backup_process.get_size())}'
                
                else:
                    logger.warning('backup failed')
                    self.last_backup_status += 'failed'
                
                logger.debug(f'backup_status: {self.last_backup_status}')
                self.last_backup_time = time.time()
                self.backup_process = None
                self.update_buttons()
                self.schedule_next_backup()
            
            self.update_status()
            self.after(500, self.monitor_backup)
    
    def cancel_backup(self):
        self.last_user_action_time = time.time()
        if self.backup_is_running():
            self.backup_process.cancel()
            self.backup_process = None
            self.schedule_next_backup()
            
        self.update_buttons()
        self.update_status()
    
    def can_backup(self):
        if time.time() - self.can_backup_last_poll > 1:
            self.can_backup_last = self.checker.can_backup()
            self.can_backup_last_poll = time.time()
        return self.can_backup_last
        
    def schedule_next_backup(self, seconds = 0):
        if not seconds:
            seconds = self.get_backup_period()
        self.next_backup_time = int(time.time() + seconds)
        logger.info(f'scheduling next backup at {self.next_backup_time} ({time.ctime(self.next_backup_time)})')
        self.config['backups']['next_at'] = str(self.next_backup_time)
        self.save_config()
    
    def get_backup_period(self) -> int: # in seconds
        return int(float(self.config['backups']['delay']) * 60 * 60)

    def get_countdown_period(self) -> int: # in seconds
        return int(float(self.config['backups']['countdown']) * 60 * 60)

    def get_hide_period(self) -> int: # in seconds
        return int(float(self.config['backups']['hide_after']) * 60 * 60)

    def save_config(self):
        logger.debug(f'saving config to "{self.config_path}"')
        with open(self.config_path, 'w') as configfile:
            self.config.write(configfile)

    def update_buttons(self):
        if self.backup_process:
            self.buttons['cancel']['state'] = tk.NORMAL
            self.buttons['start']['state'] = tk.DISABLED
            self.buttons['next_short']['state'] = tk.DISABLED
            self.buttons['next_long']['state'] = tk.DISABLED
        else:
            self.buttons['cancel']['state'] = tk.DISABLED
            self.buttons['start']['state'] = tk.NORMAL
            self.buttons['next_short']['state'] = tk.NORMAL
            self.buttons['next_long']['state'] = tk.NORMAL

    def timestamp2string(self, timestamp):
        return time.strftime('%d.%m %H:%M', time.localtime(timestamp))

    def timedelta2string(self, t: timedelta):
        vals = [[t.days, 'day'], [t.seconds // 3600, 'hour'], [t.seconds // 60 % 60, 'minute']]
        s = ' '.join([f'{f[0]} {f[1]}{"s" if f[0] > 1 else ""}' for f in vals if f[0]])
        if not s:
            s = 'less than a minute'
        return s

    def update_status(self):
        self.update_backup_status()
        self.update_log_status()

        if self.backup_is_running():
            self.set_title()
        elif not self.can_backup():
            self.set_title('preconditions not met')
        elif self.is_in_countdown():
            self.set_title('in countdown')
        else:
            self.set_title()

    def update_backup_status(self):
        text = ''
        if self.backup_is_running():
            p = self.backup_process.get_progress() if self.backup_process.is_running() else 0
            s = f'at {self.backup_process.get_speed()}, ' if self.backup_process.is_running() else ''
            text += f'backing-up your files {s}{int(p * 100)}% done'
        
        else:
            before = self.next_backup_time > time.time()
            text += f'next backup {"is" if before else "was"} scheduled at {self.timestamp2string(self.next_backup_time)}'
            
            if before:
                s = self.timedelta2string(datetime.fromtimestamp(self.next_backup_time) - datetime.now())
                text += f' ({s} from now)'
            
            elif not self.can_backup():
                text += ' and will start as soon as possible'

        self.status[0].set(text)
    
    def update_log_status(self):
        text = ''
        if self.last_backup_time:
            text += f'last backup finished at {self.timestamp2string(self.last_backup_time)}'
            if self.last_backup_status:
                text += f', {self.last_backup_status}'
        self.status[1].set(text)

    def invalid_action(self, message):
        t = tk.Toplevel(self)
        t.wm_title('invalid action')
        
        f = tk.Frame(t)
        tk.Label(f, text=message).pack()
        f.pack(side="top", fill="both", expand=True, padx=100, pady=10)

        f = tk.Frame(t)
        tk.Button(f, text="Ok", width=10, command=t.destroy).pack(padx=2, pady=2, side=tk.LEFT)
        f.pack()
    
    def close_handler(self):
        t = tk.Toplevel(self)
        t.wm_title('confirm close')
        
        f = tk.Frame(t)
        m = 'when you click "Yes" the background service will stop\n'
        m += 'you can restart it by executing "systemctl start backup-utility.service" as root'
        tk.Label(f, text=m).pack()
        f.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        f = tk.Frame(t)
        tk.Button(f, text="Yes", width=10, command=self.terminate).pack(padx=2, pady=2, side=tk.LEFT)
        tk.Button(f, text="No", width=10, command=t.destroy).pack(padx=2, pady=2, side=tk.LEFT)
        f.pack()

    
    def terminate(self):
        if self.backup_is_running():
            self.cancel_backup()
        self.destroy()

    def hide_user(self):
        self.last_user_action_time = time.time()
        self.hide()

    def hide(self):
        if not self.hidden:
            self.hidden = True
            self.iconify()
            time.sleep(0.5)
            self.withdraw()

    def unhide(self):
        if self.hidden:
            self.hidden = False
            self.iconify()
            self.last_unhide_time = time.time()


if __name__ == '__main__':
    GUI(default_config_file_path, rsyncProcess).mainloop()
