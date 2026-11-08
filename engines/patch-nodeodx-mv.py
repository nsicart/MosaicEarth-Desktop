"""Keep mv's directory path from racing its asynchronously opened file stream.

The pinned mv dependency first tries to stream a directory into a file on EXDEV.
Its read error then removes that destination and copies the directory. A delayed
write-stream open can recreate the file after removal (observed on Docker/NTFS),
causing EEXIST or a successful callback followed by ENOTDIR in task initialization.
Determine the source type before opening any streams. Preserve mv's file handling,
clobber behavior, concurrency limit and original license.
"""
from pathlib import Path

path = Path('/var/www/node_modules/mv/index.js')
source = path.read_text()
old = 'function moveFileAcrossDevice(source, dest, clobber, limit, cb) {\n'
new = '''function moveFileAcrossDevice(source, dest, clobber, limit, cb) {
  fs.lstat(source, function(err, stat) {
    if (err) return cb(err);
    if (stat.isDirectory()) {
      return moveDirAcrossDevice(source, dest, clobber, limit, cb);
    }
    copyFileAcrossDevice(source, dest, clobber, limit, cb);
  });
}

function copyFileAcrossDevice(source, dest, clobber, limit, cb) {
'''
assert source.count(old) == 1, 'Unexpected pinned mv implementation'
path.write_text(source.replace(old, new))
