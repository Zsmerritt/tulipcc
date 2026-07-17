# fs_create.py

from littlefs import lfs
import os
import sys

if(len(sys.argv)<2):
    print("Usage: python fs_create.py (amyboard,tulip) [flash]")

distro = sys.argv[1]
if(distro=='tulip'):
    os.chdir('esp32s3')
else:
    os.chdir('amyboard')

idf_path = os.environ["IDF_PATH"]  # get value of IDF_PATH from environment
parttool_dir = os.path.join(idf_path, "components", "partition_table")  # parttool.py lives in $IDF_PATH/components/partition_table

sys.path.append(parttool_dir)  # this enables Python to find parttool module
from parttool import *  # import all names inside parttool module
import gen_esp32part as gen

if(not os.path.exists('build/flash_args')):
    print("Run this after a successful build only")
    sys.exit()

SYSTEM_HOME = "../fs/%s" % (distro)

# Copy over only these extensions (compared case-insensitively, so .MID
# and .mid both match).
good_exts = [".txt", ".png", ".py", ".json", ".obj", ".wav", ".mid"]
# And these folders
source_folders = ['app','ex','im']

# Get the partition info from the built partition table
partition_table_file = "build/partition_table/partition-table.bin"
with open(partition_table_file, 'rb') as f:
    partition_table = gen.PartitionTable.from_binary(f.read())
vfs_partition = partition_table.find_by_name('vfs')
sys_partition = partition_table.find_by_name('system')

def copy_to_lfs(source, dest):
    #print("Copying %s to %s" % (source, dest))
    source_data = open(source, "rb").read()
    fh = lfs.file_open(fs, dest, "wb")
    lfs.file_write(fs, fh, source_data)
    lfs.file_close(fs, fh)

# First make an empty VFS for the user filesystem
cfg = lfs.LFSConfig(block_size=4096, block_count = int(vfs_partition.size / 4096),  disk_version=0x00020000)
fs = lfs.LFSFilesystem()
lfs.format(fs, cfg)
lfs.mount(fs, cfg)
copy_to_lfs('boot.py', 'boot.py')

print("writing VFS .bin file...")
with open("build/%s-vfs.bin" % (distro),"wb") as fh:
    fh.write(cfg.user_context.buffer)
print("... done.")

cur_dir = os.getcwd()
os.chdir(SYSTEM_HOME)
folders = [x[0][2:] for x in os.walk('.')][1:]

cfg = lfs.LFSConfig(block_size=4096, block_count = int(sys_partition.size / 4096),  disk_version=0x00020000)
fs = lfs.LFSFilesystem()
lfs.format(fs, cfg)
lfs.mount(fs, cfg)

for folder in folders:
    lfs.mkdir(fs,folder)
    for file in os.listdir(folder):
        file_part, ext = os.path.splitext(file)
        if(ext.lower() in good_exts):
            copy_to_lfs(folder+'/'+file, folder+'/'+file)

os.chdir(cur_dir)

print("writing sys .bin file...")
with open("build/%s-sys.bin" % (distro),"wb") as fh:
    fh.write(cfg.user_context.buffer)
print("... done.")


# Gamma9001 drum banks: generate drums.bin from the amy submodule and flash it
# into the `drums` partition (AMY mmaps it at boot; see amy_connector.c).
drums_partition = None
try:
    drums_partition = partition_table.find_by_name('drums')
except Exception:
    pass
if drums_partition is not None:
    import subprocess
    subprocess.check_call([sys.executable, '-m', 'amy.headers', 'gamma9001'], cwd='../../amy')
    drums_bin = open('../../amy/build/drums.bin', 'rb').read()
    if len(drums_bin) > drums_partition.size:
        raise SystemExit("drums.bin (%d bytes) does not fit the drums partition (%d bytes)"
                         % (len(drums_bin), drums_partition.size))
    with open('build/%s-drums.bin' % (distro), 'wb') as fh:
        fh.write(drums_bin)
    print("drums.bin: %d bytes into %s partition at %s" % (
        len(drums_bin), 'drums', hex(drums_partition.offset)))

# GM SoundFont banks: assemble the checked-in blobs from the amy submodule
# into the `fonts` partition image (AMY mmaps it at boot; see amy_connector.c).
# Layout must match the amy maps: GeneralUser bank (pcm_gm.h) at 0, the big
# multi-font bank (pcm_gm_big.h) at 0x4B0000. Keep GM_BIG_OFFSET below in
# lockstep with GM_BIG_BYTE_OFFSET in tulip/shared/amy_connector.c and the
# `fonts` partition in boards/N32R8/tulip-partitions-32MB.csv.
fonts_partition = None
try:
    fonts_partition = partition_table.find_by_name('fonts')
except Exception:
    pass
if fonts_partition is not None:
    GM_BIG_OFFSET = 0x4B0000
    small = open('../../amy/sounds/gm/fonts.bin', 'rb').read()
    if len(small) > GM_BIG_OFFSET:
        raise SystemExit("fonts.bin (%d bytes) overruns the big bank at 0x%x"
                         % (len(small), GM_BIG_OFFSET))
    fonts_bin = small + b'\xff' * (GM_BIG_OFFSET - len(small))
    try:
        fonts_bin += open('../../amy/sounds/gm/fonts_big.bin', 'rb').read()
    except OSError:
        print("fonts_big.bin missing; only the GeneralUser bank goes in")
    if len(fonts_bin) > fonts_partition.size:
        raise SystemExit("fonts image (%d bytes) does not fit the fonts partition (%d bytes)"
                         % (len(fonts_bin), fonts_partition.size))
    with open('build/%s-fonts.bin' % (distro), 'wb') as fh:
        fh.write(fonts_bin)
    print("fonts image: %d bytes (%d + big@0x%x) into %s partition at %s" % (
        len(fonts_bin), len(small), GM_BIG_OFFSET, 'fonts',
        hex(fonts_partition.offset)))

# Update the flash_args file to have the sys and user partitions
flash_args = open('build/flash_args','r').read().split('\n')[:-1]
flash_args.append('%s %s-sys.bin' % (hex(sys_partition.offset), distro))
flash_args.append('%s %s-vfs.bin' % (hex(vfs_partition.offset), distro))
if drums_partition is not None:
    flash_args.append('%s %s-drums.bin' % (hex(drums_partition.offset), distro))
if fonts_partition is not None:
    flash_args.append('%s %s-fonts.bin' % (hex(fonts_partition.offset), distro))
new_flash_args = open('build/flash_args_%s' % (distro),'w')
for f in flash_args:
    new_flash_args.write('%s\n' % (f))
new_flash_args.close()
os.chdir('build')
os.system('esptool.py --chip esp32s3 merge_bin -o %s.bin @flash_args_%s' % (distro, distro))
os.chdir('..')

# I don't love this but it works
# i wonder if i can get CMake to pass along MICROPY_BOARD to this program in a shell instead
MICROPY_BOARD = subprocess.check_output("grep MICROPY_BOARD build/CMakeCache.txt | cut -d '=' -f2 | awk '{print $1}'",shell=True)[:-1].decode('ascii')
os.system("mkdir -p dist")
os.system("cp build/%s.bin dist/%s-full-%s.bin" % (distro, distro, MICROPY_BOARD))
os.system("cp build/micropython.bin dist/%s-firmware-%s.bin" % (distro, MICROPY_BOARD))
os.system("cp build/%s-sys.bin dist/%s-sys.bin" %(distro, distro))

# Optionally do the flash of the whole image
if(len(sys.argv)>2):
    if(sys.argv[2]== 'flash'):
        print("Writing full image")
        os.system("esptool.py write_flash 0x0 dist/%s-full-%s.bin" % (distro, MICROPY_BOARD))

os.chdir('..')

