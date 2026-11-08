// Run inside the pinned NodeODX image with /var/www/data mounted on the host:
// cd /var/www && ./node.sh /tmp/test-nodeodx-filesystem.js
// Exercises the actual cross-filesystem move used when committing image uploads.
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const crypto = require('crypto');
const mv = require('/var/www/node_modules/mv');
const id = 'mosaicearth-fs-qa-' + crypto.randomBytes(12).toString('hex');
const sourceRoot = path.join('/var/www/tmp', id);
const targetRoot = path.join('/var/www/data', id);
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function move(source, target, expectedError) {
  let calls = 0;
  let error;
  await new Promise(resolve => mv(source, target, err => {
    calls++;
    error = err;
    resolve();
  }));
  // Observe delayed stream callbacks as well as the first completion.
  await delay(50);
  assert.strictEqual(calls, 1, 'move must complete exactly once');
  if (expectedError) assert.strictEqual(error && error.code, expectedError);
  else assert.ifError(error);
}
(async () => {
  fs.mkdirSync(sourceRoot);
  fs.mkdirSync(targetRoot);
  assert.notStrictEqual(fs.statSync(sourceRoot).dev, fs.statSync(targetRoot).dev,
    'This integration check requires different source/destination filesystems');
  try {
    for (let i = 0; i < 30; i++) {
      const source = path.join(sourceRoot, String(i));
      const target = path.join(targetRoot, String(i));
      fs.mkdirSync(path.join(source, 'nested'), {recursive: true});
      const photos = Array.from({length: 18}, (_, j) => crypto.randomBytes(1024 + j));
      photos.forEach((bytes, j) => fs.writeFileSync(path.join(source, 'nested', j + '.jpg'), bytes));
      await move(source, target);
      assert(!fs.existsSync(source));
      assert(fs.statSync(target).isDirectory());
      photos.forEach((bytes, j) => assert(fs.readFileSync(path.join(target, 'nested', j + '.jpg')).equals(bytes)));
    }
    const source = path.join(sourceRoot, 'single-file');
    const target = path.join(targetRoot, 'single-file');
    const bytes = crypto.randomBytes(8192);
    fs.writeFileSync(source, bytes);
    await move(source, target);
    assert(!fs.existsSync(source));
    assert(fs.readFileSync(target).equals(bytes));
    await move(path.join(sourceRoot, 'missing'), path.join(targetRoot, 'missing'), 'ENOENT');
    console.log(JSON.stringify({status: 'PASS', directories: 30, images: 540,
      file_move: true, missing_source: true, single_callback: true,
      source_dev: fs.statSync(sourceRoot).dev, target_dev: fs.statSync(targetRoot).dev}));
  } finally {
    fs.rmSync(sourceRoot, {recursive: true, force: true});
    fs.rmSync(targetRoot, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
