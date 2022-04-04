
## Install

tested on python 3.10, should work with 3.7 and above

### install required dependencies
- Xserver, systemd (you most likely already have these)
- python3-tkinter
- rsync (it is possible to create a class wrapping a different program)

### create the config file
create a file called `backup.conf` in the project root directory. If you use rsync paste the following into the file and fill the missing information
```
[rsync]
# source path
from = 
# target path
to = 
# this entry is optional
options = -aAX
```
once the app starts it will add additional default parameters

### run `install.py`
execute `python3 install.py` in the project root directory. This will ask some questions, defaults should be fine, it will then create a systemd service file to autostart the utility after system startup, enable and start the service


