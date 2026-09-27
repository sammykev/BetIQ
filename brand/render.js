const { chromium } = require(require('child_process').execSync('npm root -g').toString().trim() + '/playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1000, height: 1000 } });
  await p.goto('file://' + process.cwd() + '/avatar.html');
  await p.evaluate(() => document.fonts.ready);
  await p.waitForTimeout(800);
  await p.screenshot({ path: 'betiq-profile-1000.png' });
  // How X shows it: a circle
  await p.addStyleTag({ content: 'html{background:#15202b} body{border-radius:50%}' });
  await p.screenshot({ path: 'preview-circle.png' });
  // X's recommended 400x400, scaled down from the full-size image
  const s = await b.newPage({ viewport: { width: 400, height: 400 } });
  await s.setContent(`<body style="margin:0"><img id="i" width="400" height="400" style="display:block"></body>`);
  const data = require('fs').readFileSync('betiq-profile-1000.png').toString('base64');
  await s.evaluate(d => new Promise(r => { const i = document.getElementById('i'); i.onload = r; i.src = 'data:image/png;base64,' + d; }), data);
  await s.screenshot({ path: 'betiq-profile-400.png' });
  await b.close();
})();
