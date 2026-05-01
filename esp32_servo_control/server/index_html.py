# -*- coding: utf-8 -*-
"""Web UI template for the cat robot console.

This file is intentionally kept separate from ``app.py`` so the very large
HTML / CSS / JS payload is easy to iterate on without touching the backend
code.
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Meow · Cat Robot Console</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&family=Playfair+Display:wght@500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root{
      --bg-0:#07080c;
      --bg-1:#0b0d13;
      --bg-2:#10131b;
      --bg-3:#151925;
      --surface:rgba(18,21,30,.78);
      --surface-2:rgba(24,28,40,.7);
      --surface-3:rgba(12,14,20,.6);
      --border:rgba(255,255,255,.06);
      --border-strong:rgba(255,255,255,.14);
      --border-bright:rgba(255,255,255,.24);
      --fg:#eef1f7;
      --fg-dim:#8a93a7;
      --fg-soft:#b6bdcd;
      --fg-muted:#5b6378;
      --accent-1:#7c8cff;
      --accent-2:#c56bff;
      --accent-3:#ff7ab6;
      --accent-grad:linear-gradient(135deg,#7c8cff 0%,#a470ff 55%,#ff7ab6 100%);
      --glow:0 0 0 1px rgba(124,140,255,.35),0 8px 28px rgba(124,140,255,.25);
      --good:#4ade80;
      --warn:#fbbf24;
      --bad:#f87171;
      --rec:#ff4d6d;
      --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
      --sans:'Inter',ui-sans-serif,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
      --serif:'Playfair Display',Georgia,serif;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    *::selection{background:rgba(124,140,255,.35);color:#fff}
    html,body{height:100%}
    html{scroll-behavior:smooth}
    body{
      font-family:var(--sans);
      color:var(--fg);
      font-size:14px;
      letter-spacing:.005em;
      -webkit-font-smoothing:antialiased;
      text-rendering:optimizeLegibility;
      background:var(--bg-0);
      min-height:100vh;
      overflow-x:hidden;
    }
    body::before{
      content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
      background:
        radial-gradient(1100px 700px at 5% -10%,rgba(124,140,255,.14),transparent 60%),
        radial-gradient(900px 600px at 108% 5%,rgba(197,107,255,.10),transparent 55%),
        radial-gradient(900px 700px at 50% 115%,rgba(255,122,182,.08),transparent 60%);
    }
    body::after{
      content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.45;
      background-image:
        linear-gradient(rgba(255,255,255,.015) 1px,transparent 1px),
        linear-gradient(90deg,rgba(255,255,255,.015) 1px,transparent 1px);
      background-size:48px 48px;
      mask-image:radial-gradient(ellipse at center,#000 30%,transparent 80%);
    }
    main{position:relative;z-index:1}
    /* ===== Nav ===== */
    .nav{
      position:sticky;top:0;z-index:40;
      display:flex;align-items:center;gap:18px;
      padding:14px 28px;
      border-bottom:1px solid var(--border);
      background:rgba(7,8,12,.72);
      backdrop-filter:blur(18px) saturate(140%);
    }
    .brand{display:flex;align-items:center;gap:12px}
    .brand-logo{
      width:32px;height:32px;border-radius:9px;
      background:var(--accent-grad);
      position:relative;overflow:hidden;
      box-shadow:0 6px 16px rgba(124,140,255,.35);
    }
    .brand-logo::after{
      content:"";position:absolute;inset:4px;border-radius:6px;
      background:radial-gradient(circle at 30% 30%,rgba(255,255,255,.7),transparent 55%);
    }
    .brand-name{font-weight:600;font-size:15px;letter-spacing:.02em}
    .brand-name em{font-style:normal;font-family:var(--serif);font-weight:500;color:var(--fg-soft);margin-left:6px}
    .nav-sep{flex:1}
    .nav-links{display:flex;align-items:center;gap:4px}
    .nav-links a{
      padding:7px 12px;border-radius:8px;font-size:13px;color:var(--fg-dim);
      text-decoration:none;transition:all .2s ease;
    }
    .nav-links a:hover{color:var(--fg);background:rgba(255,255,255,.04)}
    .nav-links a.active{color:var(--fg);background:rgba(255,255,255,.06)}
    .nav-actions{display:flex;align-items:center;gap:10px}
    .pill{
      display:inline-flex;align-items:center;gap:8px;
      padding:6px 12px;border-radius:999px;
      background:rgba(255,255,255,.035);
      border:1px solid var(--border);
      font-size:12px;color:var(--fg-soft);
      font-feature-settings:"tnum";
      transition:all .25s ease;
    }
    .pill .dot{width:7px;height:7px;border-radius:50%;background:#444c60;position:relative;transition:all .25s}
    .pill .dot::after{
      content:"";position:absolute;inset:-4px;border-radius:50%;
      border:1px solid transparent;transition:all .25s;
    }
    .pill.on{border-color:rgba(74,222,128,.32);background:rgba(74,222,128,.05);color:#d8ffe7}
    .pill.on .dot{background:var(--good);box-shadow:0 0 10px var(--good)}
    .pill.off{border-color:rgba(248,113,113,.28);background:rgba(248,113,113,.04);color:#ffd3d8}
    .pill.off .dot{background:var(--bad)}
    .pill.rec{border-color:rgba(255,77,109,.35);background:rgba(255,77,109,.08);color:#ffd9e1}
    .pill.rec .dot{background:var(--rec);animation:pulse 1.4s ease-in-out infinite}
    .pill.rec .dot::after{border-color:rgba(255,77,109,.4);animation:ripple 1.4s ease-out infinite}
    @keyframes pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.25);opacity:.7}}
    @keyframes ripple{0%{transform:scale(1);opacity:.9}100%{transform:scale(2.2);opacity:0}}
    /* ===== Hero ===== */
    .hero{padding:44px 28px 20px;position:relative}
    .hero-inner{max-width:1380px;margin:0 auto}
    .eyebrow{
      display:inline-flex;align-items:center;gap:10px;
      padding:5px 12px 5px 5px;border-radius:999px;
      background:rgba(255,255,255,.03);border:1px solid var(--border);
      font-size:12px;color:var(--fg-soft);margin-bottom:18px;
    }
    .eyebrow .badge{
      padding:3px 8px;border-radius:999px;font-weight:600;font-size:11px;
      background:var(--accent-grad);color:#fff;letter-spacing:.03em;
    }
    h1.display{margin-bottom:14px;line-height:1.2;padding-bottom:6px}
    h1.display .zh{
      display:block;
      font-family:var(--sans);
      font-weight:800;
      font-size:clamp(40px,5vw,68px);
      letter-spacing:-.01em;
      line-height:1.18;
      padding:2px 0 4px;
    }
    h1.display .zh em{
      font-style:normal;font-weight:800;
      background:var(--accent-grad);-webkit-background-clip:text;background-clip:text;color:transparent;
      margin-left:4px;
    }
    h1.display .en{
      display:block;margin-top:4px;
      font-family:var(--serif);font-style:italic;font-weight:600;
      font-size:clamp(18px,1.8vw,24px);
      letter-spacing:.01em;line-height:1.35;
      background:var(--accent-grad);-webkit-background-clip:text;background-clip:text;color:transparent;
      padding:2px 0 4px;
    }
    .lede{max-width:720px;color:var(--fg-soft);font-size:15.5px;line-height:1.65}
    .hero-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:32px}
    .stat{
      padding:18px 18px 16px;border-radius:14px;
      background:var(--surface);border:1px solid var(--border);
      backdrop-filter:blur(14px);
      position:relative;overflow:hidden;
      transition:all .3s ease;
    }
    .stat::before{content:"";position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--accent-grad);opacity:.4;transition:opacity .3s}
    .stat:hover{transform:translateY(-2px);border-color:var(--border-strong)}
    .stat:hover::before{opacity:1}
    .stat .label{font-size:11px;text-transform:uppercase;letter-spacing:.12em;color:var(--fg-dim);margin-bottom:8px}
    .stat .val{font-family:var(--mono);font-size:26px;font-weight:500;letter-spacing:-.01em}
    .stat .trend{font-size:12px;color:var(--fg-dim);margin-top:4px}
    .stat .trend b{color:var(--fg);font-weight:500}
    @media (max-width:780px){ .hero-stats{grid-template-columns:repeat(2,1fr)} }
    /* ===== Layout ===== */
    .section{padding:14px 28px 44px}
    .shell{max-width:1380px;margin:0 auto}
    .grid-main{display:grid;grid-template-columns:1.15fr .85fr;gap:22px}
    @media (max-width:1080px){ .grid-main{grid-template-columns:1fr} }
    .stack{display:flex;flex-direction:column;gap:22px;min-width:0}
    /* ===== Card ===== */
    .card{
      position:relative;
      border-radius:18px;
      border:1px solid var(--border);
      background:linear-gradient(180deg,rgba(24,28,40,.72),rgba(14,17,25,.82));
      backdrop-filter:blur(22px) saturate(130%);
      box-shadow:0 22px 60px rgba(0,0,0,.34),inset 0 1px 0 rgba(255,255,255,.04);
      overflow:hidden;
    }
    .card::before{
      content:"";position:absolute;top:0;left:0;right:0;height:1px;
      background:linear-gradient(90deg,transparent,rgba(255,255,255,.18),transparent);
      opacity:.8;pointer-events:none;
    }
    .card-head{
      display:flex;align-items:center;gap:14px;
      padding:18px 22px 14px;
    }
    .card-head .tag{
      display:inline-flex;align-items:center;justify-content:center;
      width:30px;height:30px;border-radius:10px;
      background:linear-gradient(135deg,rgba(124,140,255,.2),rgba(197,107,255,.16));
      border:1px solid rgba(124,140,255,.28);
    }
    .card-head .tag svg{width:15px;height:15px;color:#d6dcff}
    .card-head .title{font-size:14.5px;font-weight:600;letter-spacing:.005em}
    .card-head .subtitle{font-size:12px;color:var(--fg-dim);margin-top:2px}
    .card-head .side{margin-left:auto;display:flex;align-items:center;gap:8px}
    .card-body{padding:8px 22px 22px}
    .card-divider{height:1px;background:var(--border);margin:0 22px}
    .section-title{
      font-size:11px;text-transform:uppercase;letter-spacing:.14em;
      color:var(--fg-dim);margin:16px 0 10px;font-weight:600;
    }
    /* ===== Buttons ===== */
    .btn{
      appearance:none;border:1px solid var(--border-strong);
      background:rgba(255,255,255,.025);
      color:var(--fg);padding:9px 14px;border-radius:10px;
      font-family:inherit;font-size:13px;font-weight:500;
      cursor:pointer;display:inline-flex;align-items:center;gap:8px;
      transition:all .2s ease;white-space:nowrap;
    }
    .btn:hover{background:rgba(255,255,255,.06);border-color:var(--border-bright);transform:translateY(-1px)}
    .btn:active{transform:translateY(0)}
    .btn svg{width:14px;height:14px;flex-shrink:0}
    .btn.sm{padding:7px 10px;font-size:12px}
    .btn.lg{padding:11px 18px;font-size:14px}
    .btn.primary{
      background:var(--accent-grad);border-color:transparent;
      color:#fff;font-weight:600;
      box-shadow:0 8px 22px rgba(124,140,255,.32);
    }
    .btn.primary:hover{filter:brightness(1.1);box-shadow:0 12px 32px rgba(124,140,255,.45)}
    .btn.ghost{background:transparent;border-color:var(--border)}
    .btn.danger{color:#ffd3d8;border-color:rgba(248,113,113,.32);background:rgba(248,113,113,.04)}
    .btn.danger:hover{background:rgba(248,113,113,.1);border-color:rgba(248,113,113,.48)}
    .btn.rec{
      background:linear-gradient(135deg,#ff4d6d,#ff7ab6);
      border-color:transparent;color:#fff;font-weight:600;
      box-shadow:0 8px 22px rgba(255,77,109,.32);
    }
    .btn.rec:hover{filter:brightness(1.08);box-shadow:0 12px 30px rgba(255,77,109,.45)}
    .btn.rec.on{background:rgba(255,77,109,.08);border:1px solid rgba(255,77,109,.5);color:#ffd3dc;box-shadow:none}
    .btn.rec.on:hover{background:rgba(255,77,109,.14)}
    .btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
    /* ===== Performance Mode（极速画面模式） ===== */
    .btn.perf{
      background:linear-gradient(135deg,#3b2a12 0%,#6b4a13 100%);
      border:1px solid rgba(255,196,86,.45);
      color:#ffe3a3;
      box-shadow:0 6px 20px rgba(255,196,86,.18);
      font-weight:600;
    }
    .btn.perf:hover{filter:brightness(1.12);border-color:rgba(255,196,86,.7)}
    .btn.perf.on{
      background:linear-gradient(135deg,#ffb347 0%,#ff6a6a 100%);
      color:#1a0a00;
      border:1px solid rgba(255,255,255,.55);
      box-shadow:0 8px 26px rgba(255,128,80,.55),inset 0 0 0 1px rgba(255,255,255,.15);
    }
    .btn.perf.on:hover{filter:brightness(1.05)}
    .perf-banner{
      position:sticky;top:57px;z-index:39;
      margin:0;padding:10px 28px;
      background:linear-gradient(90deg,rgba(255,179,71,.18) 0%,rgba(255,106,106,.22) 100%);
      border-bottom:1px solid rgba(255,196,86,.35);
      backdrop-filter:blur(12px) saturate(140%);
      display:none;align-items:center;gap:14px;
      font-size:13px;color:#ffe8c2;
    }
    .perf-banner.on{display:flex}
    .perf-banner .dot{
      width:8px;height:8px;border-radius:50%;background:#ffb347;
      box-shadow:0 0 0 4px rgba(255,179,71,.2);
      animation:perfPulse 1.4s ease-in-out infinite;
    }
    @keyframes perfPulse{
      0%,100%{box-shadow:0 0 0 4px rgba(255,179,71,.18)}
      50%{box-shadow:0 0 0 8px rgba(255,179,71,.05)}
    }
    .perf-banner b{color:#fff;font-weight:600}
    .perf-banner .tag{
      font-family:var(--mono);font-size:11px;letter-spacing:.08em;
      padding:3px 8px;border-radius:6px;
      background:rgba(255,255,255,.08);border:1px solid rgba(255,196,86,.35);color:#ffe3a3;
    }
    /* 开启极速模式时把其它控制面板做一点点暗化，提醒用户 */
    body.perf-on .card:not(#vision){opacity:.42;filter:saturate(.7);pointer-events:none;transition:opacity .2s ease}
    body.perf-on .hero{opacity:.55;transition:opacity .2s ease}
    .icon-btn{
      width:34px;height:34px;border-radius:9px;
      display:inline-flex;align-items:center;justify-content:center;
      background:rgba(255,255,255,.03);border:1px solid var(--border);
      color:var(--fg-soft);cursor:pointer;transition:all .2s ease;
    }
    .icon-btn:hover{background:rgba(255,255,255,.07);color:var(--fg);border-color:var(--border-bright)}
    .icon-btn svg{width:15px;height:15px}
    .btn-row{display:flex;gap:8px;flex-wrap:wrap}
    .btn-row.tight{gap:6px}
    /* ===== Inputs ===== */
    .input,.textarea{
      width:100%;border:1px solid var(--border-strong);border-radius:10px;
      background:rgba(255,255,255,.025);color:var(--fg);
      padding:10px 12px;font-size:13.5px;outline:none;font-family:inherit;
      transition:all .2s;
    }
    .input:focus,.textarea:focus{border-color:rgba(124,140,255,.55);background:rgba(124,140,255,.05);box-shadow:0 0 0 4px rgba(124,140,255,.08)}
    .input::placeholder,.textarea::placeholder{color:var(--fg-muted)}
    .textarea{min-height:76px;resize:vertical;line-height:1.5}
    .input-group{position:relative;display:flex;align-items:center;gap:10px}
    .input-group .input{padding-left:38px}
    .input-group svg.leading{position:absolute;left:12px;width:16px;height:16px;color:var(--fg-dim);pointer-events:none}
    /* ===== Vision ===== */
    .vision-frame{position:relative;border-radius:14px;overflow:hidden;border:1px solid var(--border);background:#000}
    .vision-frame canvas{width:100%;aspect-ratio:4/3;display:block;background:#000}
    .vision-overlay{
      position:absolute;left:12px;top:12px;right:12px;
      display:flex;align-items:center;justify-content:space-between;
      pointer-events:none;
    }
    .vision-overlay .chip{
      padding:5px 10px;border-radius:8px;
      background:rgba(0,0,0,.45);backdrop-filter:blur(6px);
      border:1px solid rgba(255,255,255,.14);
      font-size:11px;color:rgba(255,255,255,.85);font-family:var(--mono);
    }
    .vision-overlay .live{
      display:inline-flex;align-items:center;gap:6px;
      color:#ffc9d2;border-color:rgba(255,77,109,.45);background:rgba(255,77,109,.22);
    }
    .vision-overlay .live::before{content:"";width:6px;height:6px;border-radius:50%;background:#ff4d6d;animation:pulse 1.4s ease-in-out infinite}
    .vision-scan{
      position:absolute;left:0;right:0;height:2px;top:0;pointer-events:none;
      background:linear-gradient(90deg,transparent,rgba(124,140,255,.55),transparent);
      animation:scan 4s ease-in-out infinite;opacity:.55;
    }
    @keyframes scan{0%,100%{top:0;opacity:0}10%{opacity:.6}50%{top:98%;opacity:.6}60%{opacity:0}}
    /* ===== Chat ===== */
    .chat{
      background:rgba(7,9,14,.6);border:1px solid var(--border);
      border-radius:12px;padding:4px;
      min-height:220px;max-height:320px;overflow:hidden;
      display:flex;flex-direction:column;
    }
    .chat-body{padding:10px 14px;overflow-y:auto;flex:1}
    .chat-body::-webkit-scrollbar{width:6px}
    .chat-body::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:3px}
    .chat-line{
      padding:7px 0;border-bottom:1px solid var(--border);
      font-size:13px;line-height:1.6;color:var(--fg-soft);
      display:flex;gap:8px;align-items:flex-start;
    }
    .chat-line:last-child{border-bottom:none}
    .chat-line .who{font-size:10.5px;color:var(--fg-dim);font-weight:500;text-transform:uppercase;letter-spacing:.1em;flex-shrink:0;min-width:46px;padding-top:2px;font-family:var(--mono)}
    .chat-line .msg{flex:1;word-break:break-word}
    .chat-line.ai .msg{color:#d2dbff}
    .chat-line.rec .msg{color:#ffd9e1}
    .chat-line.rec .who{color:#ff9eb3}
    .chat-partial{padding:8px 0;color:var(--fg-dim);font-style:italic;display:flex;align-items:center;gap:8px}
    .chat-partial::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--accent-1);animation:pulse 1.2s ease-in-out infinite}
    /* ===== Records ===== */
    .rec-panel{padding:8px 22px 22px}
    .rec-hero{
      display:flex;align-items:stretch;gap:14px;flex-wrap:wrap;
      padding:16px;border-radius:14px;margin-bottom:16px;
      background:radial-gradient(600px 200px at 0% 0%,rgba(255,77,109,.08),transparent 70%),rgba(255,255,255,.02);
      border:1px solid var(--border-strong);
    }
    .rec-hero .info{flex:1;min-width:200px}
    .rec-hero .info h3{font-family:var(--serif);font-weight:500;font-size:20px;letter-spacing:-.005em;margin-bottom:4px}
    .rec-hero .info p{color:var(--fg-dim);font-size:12.5px;line-height:1.55;max-width:380px}
    .rec-hero .actions{display:flex;align-items:center;gap:10px}
    .rec-meta{
      display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px;
    }
    .rec-meta .m{
      padding:10px 12px;border-radius:10px;
      background:rgba(255,255,255,.02);border:1px solid var(--border);
    }
    .rec-meta .m .k{font-size:10.5px;color:var(--fg-dim);letter-spacing:.1em;text-transform:uppercase;margin-bottom:4px}
    .rec-meta .m .v{font-family:var(--mono);font-size:15px;font-weight:500}
    .rec-ask{display:flex;gap:8px;margin-bottom:14px}
    .timeline{display:flex;flex-direction:column;gap:12px;max-height:540px;overflow-y:auto;padding-right:4px;scroll-behavior:smooth}
    .timeline::-webkit-scrollbar{width:6px}
    .timeline::-webkit-scrollbar-thumb{background:rgba(255,255,255,.12);border-radius:3px}
    .entry{
      display:grid;grid-template-columns:108px 1fr auto;gap:14px;
      padding:12px;border:1px solid var(--border);border-radius:14px;
      background:linear-gradient(180deg,rgba(255,255,255,.028),rgba(255,255,255,.01));
      animation:slideIn .4s cubic-bezier(.2,.9,.3,1);
      transition:all .25s ease;
    }
    .entry:hover{border-color:var(--border-strong);transform:translateX(2px)}
    @keyframes slideIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
    .entry .thumb{
      position:relative;width:108px;height:81px;border-radius:10px;overflow:hidden;
      background:#0a0d14;cursor:pointer;border:1px solid var(--border);
    }
    .entry .thumb img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .35s ease}
    .entry .thumb:hover img{transform:scale(1.08)}
    .entry .thumb::after{
      content:"";position:absolute;inset:0;
      background:linear-gradient(180deg,transparent 60%,rgba(0,0,0,.4));
      pointer-events:none;
    }
    .entry .meta{min-width:0;display:flex;flex-direction:column;gap:4px;justify-content:center}
    .entry .time{font-family:var(--mono);font-size:11.5px;color:var(--fg-dim);letter-spacing:.04em}
    .entry .desc{font-size:13.5px;color:var(--fg);line-height:1.5;word-break:break-word}
    .entry .idx{font-family:var(--mono);font-size:10.5px;color:var(--fg-muted);align-self:flex-start;padding:3px 7px;background:rgba(255,255,255,.04);border-radius:6px;letter-spacing:.05em}
    .empty{
      padding:42px 20px;text-align:center;color:var(--fg-dim);font-size:13px;
      border:1px dashed var(--border-strong);border-radius:14px;
      background:linear-gradient(180deg,rgba(255,255,255,.015),transparent);
    }
    .empty svg{width:36px;height:36px;margin:0 auto 12px;color:var(--fg-muted);display:block}
    .empty .emph{color:var(--fg);font-weight:500;display:block;margin-bottom:4px;font-size:14px}
    /* ===== Analysis ===== */
    .analysis{
      margin-top:18px;padding:20px;border:1px solid var(--border);
      border-radius:16px;
      background:radial-gradient(400px 200px at 100% 0%,rgba(124,140,255,.08),transparent 70%),rgba(255,255,255,.015);
    }
    .analysis h3{font-size:11px;color:var(--fg-dim);letter-spacing:.14em;text-transform:uppercase;margin:18px 0 10px;font-weight:600;display:flex;align-items:center;gap:8px}
    .analysis h3:first-child{margin-top:0}
    .analysis h3 .num{background:rgba(124,140,255,.15);color:#c2cbff;padding:2px 7px;border-radius:6px;font-size:10px;font-family:var(--mono)}
    .analysis-summary{
      font-size:14px;line-height:1.65;color:var(--fg);padding:14px 16px;
      background:rgba(255,255,255,.025);border-left:2px solid var(--accent-1);
      border-radius:0 10px 10px 0;margin-bottom:4px;
    }
    .gantt{display:flex;flex-direction:column;gap:8px}
    .gantt-row{display:grid;grid-template-columns:108px 1fr;gap:12px;align-items:center;font-size:12px}
    .gantt-row .lbl{color:var(--fg-soft);font-family:var(--mono);font-size:11.5px}
    .gantt-track{position:relative;height:26px;background:rgba(255,255,255,.035);border-radius:7px;border:1px solid var(--border);overflow:hidden}
    .gantt-track::before{
      content:"";position:absolute;inset:0;
      background:linear-gradient(90deg,transparent,rgba(255,255,255,.05) 50%,transparent);
      background-size:200% 100%;opacity:.5;
    }
    .gantt-bar{
      position:absolute;top:3px;bottom:3px;border-radius:5px;
      background:var(--accent-grad);
      display:flex;align-items:center;padding:0 10px;
      font-size:11.5px;color:#fff;white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis;
      box-shadow:0 2px 10px rgba(124,140,255,.28);
      animation:barFill .6s cubic-bezier(.2,.9,.3,1);
    }
    @keyframes barFill{from{width:0;opacity:0}to{opacity:1}}
    .chart-wrap{position:relative;height:240px;margin-top:4px;padding:8px;border-radius:10px;background:rgba(255,255,255,.01)}
    .table{width:100%;border-collapse:collapse;font-size:13px}
    .table th,.table td{padding:10px 12px;border-bottom:1px solid var(--border);text-align:left;vertical-align:top}
    .table th{color:var(--fg-dim);font-weight:500;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;padding-bottom:8px}
    .table tbody tr{transition:background .2s}
    .table tbody tr:hover{background:rgba(255,255,255,.025)}
    .table td.t{color:var(--fg-soft);font-family:var(--mono);font-size:12px;white-space:nowrap}
    .table td.cat{color:#c2cbff}
    .table td.min{font-family:var(--mono);color:var(--fg)}
    /* ===== Controls ===== */
    .grid-controls{display:grid;grid-template-columns:1.1fr .9fr;gap:22px;margin-top:22px}
    @media (max-width:1080px){ .grid-controls{grid-template-columns:1fr} }
    .slider-row{display:grid;grid-template-columns:100px 1fr 54px;gap:12px;align-items:center;padding:7px 0;border-bottom:1px solid var(--border)}
    .slider-row:last-child{border-bottom:none}
    .slider-row label{font-size:12px;color:var(--fg-dim)}
    .slider-row .val{font-size:12.5px;text-align:right;color:var(--fg);font-family:var(--mono)}
    input[type=range]{
      -webkit-appearance:none;appearance:none;height:4px;border-radius:999px;
      background:linear-gradient(90deg,var(--accent-1),rgba(255,255,255,.08) 50%);
      outline:none;cursor:pointer;
    }
    input[type=range]::-webkit-slider-thumb{
      -webkit-appearance:none;width:15px;height:15px;border-radius:50%;
      background:#fff;cursor:pointer;
      box-shadow:0 0 0 3px rgba(124,140,255,.3),0 2px 6px rgba(0,0,0,.4);
      transition:all .2s;
    }
    input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.15);box-shadow:0 0 0 5px rgba(124,140,255,.35),0 2px 8px rgba(0,0,0,.45)}
    .chip-row{display:flex;flex-wrap:wrap;gap:8px}
    .chip{
      padding:7px 14px;border-radius:999px;background:rgba(255,255,255,.03);
      border:1px solid var(--border);font-size:12.5px;color:var(--fg-soft);cursor:pointer;
      transition:all .2s;display:inline-flex;align-items:center;gap:6px;
    }
    .chip:hover{background:rgba(124,140,255,.12);color:#fff;border-color:rgba(124,140,255,.5);transform:translateY(-1px)}
    .chip .emo{font-size:14px}
    .actions-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
    @media (max-width:680px){ .actions-grid{grid-template-columns:repeat(3,1fr)} }
    .actions-grid .btn{padding:9px 8px;font-size:12px;justify-content:center;text-align:center}
    .actions-grid .btn .n{font-family:var(--mono);color:var(--fg-dim);margin-right:6px;font-size:11px}
    .hint{font-size:12px;color:var(--fg-dim);line-height:1.65;margin-top:12px;padding-top:12px;border-top:1px dashed var(--border-strong)}
    .hint code{padding:1px 6px;border-radius:4px;background:rgba(255,255,255,.05);font-family:var(--mono);font-size:11px;color:#c2cbff}
    /* ===== Lightbox ===== */
    .lightbox{
      position:fixed;inset:0;background:rgba(0,0,0,.88);
      display:none;align-items:center;justify-content:center;z-index:999;padding:24px;
      animation:fadeIn .2s ease;
    }
    .lightbox.on{display:flex}
    .lightbox img{max-width:92vw;max-height:90vh;border-radius:12px;box-shadow:0 30px 80px rgba(0,0,0,.6)}
    .lightbox-close{
      position:absolute;top:20px;right:20px;width:36px;height:36px;
      display:inline-flex;align-items:center;justify-content:center;
      background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2);
      border-radius:50%;color:#fff;cursor:pointer;transition:all .2s;
    }
    .lightbox-close:hover{background:rgba(255,255,255,.18)}
    @keyframes fadeIn{from{opacity:0}to{opacity:1}}
    /* ===== Toast ===== */
    .toasts{position:fixed;bottom:24px;right:24px;z-index:1000;display:flex;flex-direction:column;gap:10px;max-width:320px}
    .toast{
      padding:12px 14px;border-radius:12px;
      background:rgba(18,21,30,.92);backdrop-filter:blur(18px);
      border:1px solid var(--border-strong);box-shadow:0 20px 40px rgba(0,0,0,.4);
      font-size:13px;color:var(--fg);
      display:flex;align-items:flex-start;gap:10px;
      animation:toastIn .3s cubic-bezier(.2,.9,.3,1);
    }
    .toast.success{border-color:rgba(74,222,128,.35)}
    .toast.error{border-color:rgba(248,113,113,.35)}
    .toast.info{border-color:rgba(124,140,255,.4)}
    .toast .ic{flex-shrink:0;margin-top:1px}
    .toast .body{flex:1;line-height:1.5}
    @keyframes toastIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:translateX(0)}}
    /* ===== Footer ===== */
    /* ===== Translate ===== */
    .tr-card .card-body{padding-top:4px}
    .tr-hero{
      display:flex;align-items:center;gap:14px;flex-wrap:wrap;
      padding:14px 16px;border-radius:14px;margin-bottom:14px;
      background:
        radial-gradient(520px 160px at 0% 0%,rgba(56,132,255,.14),transparent 70%),
        radial-gradient(500px 160px at 100% 100%,rgba(124,140,255,.12),transparent 70%),
        rgba(14,22,44,.6);
      border:1px solid rgba(80,150,240,.28);
      position:relative;overflow:hidden;
    }
    .tr-hero::before{
      content:"";position:absolute;inset:0;pointer-events:none;
      background:linear-gradient(120deg,transparent 30%,rgba(120,180,255,.08) 50%,transparent 70%);
      background-size:200% 100%;animation:trShimmer 5s linear infinite;
    }
    @keyframes trShimmer{0%{background-position:200% 0}100%{background-position:-200% 0}}
    .tr-hero .ic{
      width:36px;height:36px;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;
      background:linear-gradient(135deg,#3b6cff,#8aaaff);color:#fff;font-weight:700;font-family:var(--mono);font-size:14px;
      box-shadow:0 6px 20px rgba(60,120,240,.35);
    }
    .tr-hero .text{flex:1;min-width:180px}
    .tr-hero .text h3{font-family:var(--serif);font-weight:500;font-size:18px;letter-spacing:-.005em;margin-bottom:4px;color:#e6efff}
    .tr-hero .text p{font-size:12.5px;color:var(--fg-dim);line-height:1.55}
    .tr-hero .state{
      display:inline-flex;align-items:center;gap:7px;font-size:11.5px;padding:5px 10px;border-radius:999px;
      background:rgba(255,255,255,.03);border:1px solid var(--border);color:var(--fg-soft);font-family:var(--mono);
    }
    .tr-hero .state.on{background:rgba(60,140,240,.15);border-color:rgba(120,180,255,.45);color:#cfe1ff}
    .tr-hero .state .dot{width:6px;height:6px;border-radius:50%;background:var(--fg-muted);transition:all .2s}
    .tr-hero .state.on .dot{background:#58a4ff;box-shadow:0 0 8px #58a4ff;animation:pulse 1.6s ease-in-out infinite}
    .tr-pairs{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
    .tr-pair{
      padding:8px 14px;border-radius:10px;cursor:pointer;user-select:none;
      background:rgba(255,255,255,.03);border:1px solid var(--border);
      font-size:12.5px;color:var(--fg-soft);
      transition:all .2s;display:inline-flex;align-items:center;gap:7px;
      font-family:var(--mono);
    }
    .tr-pair:hover{background:rgba(88,164,255,.12);color:#fff;border-color:rgba(88,164,255,.55);transform:translateY(-1px)}
    .tr-pair.active{
      background:linear-gradient(135deg,rgba(60,120,240,.25),rgba(124,140,255,.2));
      border-color:rgba(120,180,255,.6);color:#f2f7ff;
      box-shadow:0 6px 22px rgba(60,140,240,.25);
    }
    .tr-pair .lbl{font-family:var(--sans);font-size:12.5px}
    .tr-panel{
      position:relative;border-radius:14px;padding:14px;min-height:132px;
      background:linear-gradient(180deg,rgba(8,16,36,.85),rgba(4,10,26,.9));
      border:1px solid rgba(88,140,230,.3);overflow:hidden;
    }
    .tr-panel::before{
      content:"";position:absolute;inset:0;pointer-events:none;
      background:
        repeating-linear-gradient(0deg,rgba(88,164,255,.04) 0 1px,transparent 1px 18px),
        repeating-linear-gradient(90deg,rgba(88,164,255,.04) 0 1px,transparent 1px 18px);
      mask-image:radial-gradient(ellipse at center,#000,transparent 90%);
    }
    .tr-panel::after{
      content:"";position:absolute;left:0;right:0;height:1px;top:0;
      background:linear-gradient(90deg,transparent,rgba(120,180,255,.7),transparent);
      animation:trScan 3.2s ease-in-out infinite;
    }
    @keyframes trScan{0%,100%{top:0;opacity:0}12%{opacity:.8}50%{top:calc(100% - 1px);opacity:.8}60%{opacity:0}}
    .tr-label{
      font-size:10.5px;text-transform:uppercase;letter-spacing:.16em;color:var(--fg-dim);
      font-family:var(--mono);margin-bottom:6px;display:flex;align-items:center;gap:8px;
    }
    .tr-label .pulse{width:6px;height:6px;border-radius:50%;background:#58a4ff;box-shadow:0 0 8px #58a4ff;animation:pulse 1.4s ease-in-out infinite}
    .tr-translated{
      min-height:52px;font-size:17px;line-height:1.5;color:#e6efff;
      font-weight:500;word-break:break-word;letter-spacing:.005em;
      text-shadow:0 0 12px rgba(80,150,240,.35);
    }
    .tr-translated .cursor{display:inline-block;width:3px;height:18px;background:#9ec7ff;margin-left:3px;vertical-align:-3px;animation:trBlink 1s steps(2) infinite;border-radius:1px}
    @keyframes trBlink{50%{opacity:0}}
    .tr-source{
      margin-top:10px;padding-top:10px;border-top:1px dashed rgba(88,140,230,.3);
      font-size:12.5px;color:var(--fg-soft);line-height:1.55;word-break:break-word;
      min-height:20px;
    }
    .tr-voice-hint{
      margin-top:10px;font-size:11.5px;color:var(--fg-dim);line-height:1.55;
      padding:8px 10px;border-radius:8px;background:rgba(255,255,255,.02);border:1px solid var(--border);
    }
    .tr-voice-hint code{padding:1px 5px;border-radius:4px;background:rgba(88,164,255,.12);color:#cfe1ff;font-family:var(--mono);font-size:11px}
    /* ===== Footer ===== */
    .footer{
      max-width:1380px;margin:40px auto 0;padding:24px 28px 36px;
      border-top:1px solid var(--border);color:var(--fg-dim);font-size:12px;
      display:flex;align-items:center;gap:16px;justify-content:space-between;flex-wrap:wrap;
    }
    .footer .copy{font-family:var(--mono)}
    .footer .dots{display:flex;align-items:center;gap:8px}
    .footer .dots span{width:6px;height:6px;border-radius:50%;background:var(--fg-muted)}
    .footer .dots span.on{background:var(--good);box-shadow:0 0 6px var(--good)}
  </style>
</head>
<body>
<!-- SVG icon sprite -->
<svg width="0" height="0" style="position:absolute">
  <defs>
    <symbol id="i-cam" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="13" height="12" rx="2"/><path d="M16 11l5-3v10l-5-3"/></symbol>
    <symbol id="i-chat" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12c0 4-4 7-9 7-1.4 0-2.7-.2-3.8-.6L3 20l1.5-4.1C3.6 14.7 3 13.4 3 12c0-4 4-7 9-7s9 3 9 7z"/></symbol>
    <symbol id="i-timeline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="6" cy="7" r="2"/><circle cx="6" cy="17" r="2"/><path d="M6 9v6"/><path d="M10 7h10"/><path d="M10 17h10"/></symbol>
    <symbol id="i-sparkles" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2 2-5z"/><path d="M19 14l1 2 2 1-2 1-1 2-1-2-2-1 2-1 1-2z"/></symbol>
    <symbol id="i-servo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/><path d="M18 12h3"/><circle cx="12" cy="12" r="4"/></symbol>
    <symbol id="i-actions" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/></symbol>
    <symbol id="i-send" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l17-8-6 18-3-8-8-2z"/></symbol>
    <symbol id="i-play" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></symbol>
    <symbol id="i-pause" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></symbol>
    <symbol id="i-mic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0014 0"/><path d="M12 18v3"/></symbol>
    <symbol id="i-stop" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></symbol>
    <symbol id="i-search" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></symbol>
    <symbol id="i-close" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></symbol>
    <symbol id="i-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-11"/></symbol>
    <symbol id="i-info" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 8v.01M12 11v5"/></symbol>
    <symbol id="i-alert" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l10 17H2z"/><path d="M12 10v4M12 17v.01"/></symbol>
    <symbol id="i-empty" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h10M7 13h6"/></symbol>
    <symbol id="i-reset" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1015-6.5"/><path d="M18 3v5h-5"/></symbol>
    <symbol id="i-clear" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M6 6l1 14h10l1-14"/></symbol>
    <symbol id="i-brightness" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v3M12 19v3M4.2 4.2l2 2M17.8 17.8l2 2M2 12h3M19 12h3M4.2 19.8l2-2M17.8 6.2l2-2"/></symbol>
    <symbol id="i-volume" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10v4h4l6 5V5l-6 5z"/><path d="M17 8a6 6 0 010 8"/></symbol>
    <symbol id="i-bot" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="12" rx="3"/><path d="M8 7V4M16 7V4"/><circle cx="9.5" cy="13" r="1"/><circle cx="14.5" cy="13" r="1"/></symbol>
    <symbol id="i-translate" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5h10"/><path d="M8 3v2"/><path d="M5 9c2.5 5 6 7 8 7"/><path d="M13 9c-1 3-3 5-6 7"/><path d="M13 21l4-10 4 10"/><path d="M14.5 18h5"/></symbol>
    <symbol id="i-bolt" viewBox="0 0 24 24" fill="currentColor"><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/></symbol>
  </defs>
</svg>

<main>
  <nav class="nav">
    <div class="brand">
      <div class="brand-logo"></div>
      <div class="brand-name">Meow <em>/ 赛博小喵</em></div>
    </div>
    <div class="nav-links">
      <a href="#vision" class="active">画面</a>
      <a href="#dialog">对话</a>
      <a href="#translate">同传</a>
      <a href="#record">记录</a>
      <a href="#hardware">硬件</a>
    </div>
    <div class="nav-sep"></div>
    <div class="nav-actions">
      <span id="camStatus" class="pill off"><span class="dot"></span><span>画面未连</span></span>
      <span id="uiStatus" class="pill off"><span class="dot"></span><span>控制未连</span></span>
      <span id="recStatus" class="pill"><span class="dot"></span><span>未在记录</span></span>
      <button id="perfBtn" class="btn sm perf" onclick="togglePerformanceMode()" title="开启后将关闭对话/翻译/手势/记录/TFT屏，把所有网络和算力让给摄像头">
        <svg><use href="#i-bolt"/></svg><span id="perfBtnText">极速画面</span>
      </button>
    </div>
  </nav>

  <div id="perfBanner" class="perf-banner">
    <span class="dot"></span>
    <span class="tag">PERF · ON</span>
    <span>
      <b>极速画面模式已开启</b> —— 对话 / 同传 / 手势 / 记录 / TFT 屏推送均已暂停，所有带宽与算力用于摄像头画面。
    </span>
    <button class="btn sm" style="margin-left:auto" onclick="setPerformanceMode(false)">
      <svg><use href="#i-close"/></svg>退出极速模式
    </button>
  </div>

  <section class="hero">
    <div class="hero-inner">
      <div class="eyebrow"><span class="badge">NEW</span><span>记录分析 · 由大模型逐帧描述你的活动</span></div>
      <h1 class="display"><span class="zh">赛博<em>小喵</em></span><span class="en">Cyber Miao</span></h1>
      <p class="lede">小喵是一台桌面 ESP32 小猫机器人：实时图像、全双工语音、手势追随、表情舵机，并内置 Qwen 系列多模态模型的记录与事后分析能力。</p>
      <div class="hero-stats">
        <div class="stat">
          <div class="label">Records</div>
          <div class="val" id="heroCount">0</div>
          <div class="trend">已记录条目 · <b id="heroInterval">10</b>s 一张</div>
        </div>
        <div class="stat">
          <div class="label">Session</div>
          <div class="val" id="heroSession" style="font-size:15px">—</div>
          <div class="trend">当前会话 id</div>
        </div>
        <div class="stat">
          <div class="label">Status</div>
          <div class="val" id="heroStatus" style="font-size:17px">待命</div>
          <div class="trend">记录 / 分析 / 待命</div>
        </div>
        <div class="stat">
          <div class="label">Model</div>
          <div class="val" style="font-size:15px">Qwen-VL · Qwen-Plus</div>
          <div class="trend">视觉描述 & 活动分析</div>
        </div>
      </div>
    </div>
  </section>

  <section class="section">
    <div class="shell">
      <div class="grid-main">
        <!-- Left column -->
        <div class="stack">
          <div class="card" id="vision">
            <div class="card-head">
              <div class="tag"><svg><use href="#i-cam"/></svg></div>
              <div>
                <div class="title">实时画面</div>
                <div class="subtitle">ESP32 Camera · H.264 over WebSocket</div>
              </div>
              <div class="side">
                <button class="btn sm ghost" onclick="disconnectCamera()"><svg><use href="#i-close"/></svg>断开</button>
                <button class="btn sm primary" onclick="connectCamera()"><svg><use href="#i-play"/></svg>连接画面</button>
              </div>
            </div>
            <div class="card-body">
              <div class="vision-frame">
                <canvas id="videoCanvas" width="320" height="240"></canvas>
                <div class="vision-scan"></div>
                <div class="vision-overlay">
                  <span class="chip live">LIVE</span>
                  <span class="chip" id="visionTimer">—</span>
                </div>
              </div>
            </div>
          </div>

          <div class="card" id="dialog">
            <div class="card-head">
              <div class="tag"><svg><use href="#i-chat"/></svg></div>
              <div>
                <div class="title">语音 & 对话</div>
                <div class="subtitle">Qwen 3.5 Omni Realtime · 实时双工</div>
              </div>
              <div class="side">
                <button class="btn sm ghost" onclick="disconnectUI()"><svg><use href="#i-close"/></svg>断开</button>
                <button class="btn sm primary" onclick="connectUI()"><svg><use href="#i-mic"/></svg>连接控制</button>
              </div>
            </div>
            <div class="card-body">
              <div class="chat">
                <div class="chat-body" id="chatBox">
                  <div class="chat-partial" id="partialText">等待语音或文本输入…</div>
                </div>
              </div>
              <div class="section-title">快速发送</div>
              <div class="input-group" style="margin-bottom:10px">
                <svg class="leading"><use href="#i-chat"/></svg>
                <input id="promptText" class="input" type="text" placeholder="输入一句话，让小猫直接回复">
                <button class="btn primary" onclick="sendPrompt()"><svg><use href="#i-send"/></svg>发送</button>
              </div>
              <textarea id="repeatText" class="textarea" placeholder="想让小猫原封不动复述的文字…"></textarea>
              <div class="btn-row" style="margin-top:10px"><button class="btn" onclick="sendRepeat()"><svg><use href="#i-play"/></svg>复述一次</button></div>
            </div>
          </div>

          <div class="card tr-card" id="translate">
            <div class="card-head">
              <div class="tag"><svg><use href="#i-translate"/></svg></div>
              <div>
                <div class="title">同声传译</div>
                <div class="subtitle">Real-time Simultaneous Interpreter · Qwen Omni</div>
              </div>
              <div class="side">
                <span id="trState" class="state"><span class="dot"></span><span id="trStateText">未激活</span></span>
              </div>
            </div>
            <div class="card-body">
              <div class="tr-hero">
                <div class="ic">A⇌B</div>
                <div class="text">
                  <h3>中英同声翻译</h3>
                  <p>开启后会自动识别你说的是中文还是英文，并原封不动翻译成另一种语言播放出来；它只翻译，不会顺着内容继续回答。</p>
                </div>
              </div>
              <div class="tr-pairs" id="trPairs"></div>
              <div class="btn-row" style="margin-bottom:12px">
                <button class="btn primary" onclick="startTranslate()" id="trStartBtn"><svg><use href="#i-play"/></svg>开始同传</button>
                <button class="btn danger" onclick="stopTranslate()" id="trStopBtn"><svg><use href="#i-stop"/></svg>停止</button>
              </div>
              <div class="tr-panel">
                <div class="tr-label"><span class="pulse"></span><span>TRANSLATED · 译文</span><span id="trPairBadge" style="margin-left:auto;font-size:10.5px;color:var(--fg-soft)"></span></div>
                <div id="trTranslated" class="tr-translated"><span style="color:var(--fg-dim)">等待翻译…</span></div>
                <div class="tr-source" id="trSource"><span style="color:var(--fg-muted)">（原文将出现在这里）</span></div>
              </div>
              <div class="tr-voice-hint">
                也可以直接用语音触发：<code>帮我中英互译</code> 或 <code>translate between english and chinese</code>；结束时说 <code>退出翻译</code> 或 <code>stop translation</code>。
              </div>
            </div>
          </div>
        </div>

        <!-- Right column: records -->
        <div class="stack">
          <div class="card" id="record">
            <div class="card-head">
              <div class="tag"><svg><use href="#i-timeline"/></svg></div>
              <div>
                <div class="title">记录分析</div>
                <div class="subtitle">Observe · Narrate · Analyze</div>
              </div>
            </div>
            <div class="rec-panel">
              <div class="rec-hero">
                <div class="info">
                  <h3>让它静静记录。</h3>
                  <p>开启记录模式后，小猫不会说话，只每隔 <b id="recInterval">10</b> 秒拍一张、由大模型生成一句自然语言描述。你随时可以问它“刚刚做了什么”，或让它把这段时间画成甘特图和柱状图。</p>
                </div>
                <div class="actions">
                  <button id="recToggleBtn" class="btn rec lg" onclick="toggleRecording()">
                    <svg id="recToggleIcon"><use href="#i-play"/></svg>
                    <span id="recToggleText">开始记录</span>
                  </button>
                </div>
              </div>
              <div class="rec-meta">
                <div class="m"><div class="k">Records</div><div class="v" id="recCount">0</div></div>
                <div class="m"><div class="k">Session</div><div class="v" id="recSession" style="font-size:13px">—</div></div>
                <div class="m"><div class="k">State</div><div class="v" id="recState" style="font-size:13px">待命</div></div>
              </div>
              <div class="rec-ask">
                <div class="input-group" style="flex:1">
                  <svg class="leading"><use href="#i-search"/></svg>
                  <input id="askInput" class="input" type="text" placeholder="问我刚刚做了什么？">
                </div>
                <button class="btn primary" onclick="askRecord()"><svg><use href="#i-bot"/></svg>问问</button>
                <button class="btn" onclick="runAnalysis()"><svg><use href="#i-sparkles"/></svg>智能分析</button>
              </div>
              <div id="timeline" class="timeline">
                <div class="empty">
                  <svg><use href="#i-empty"/></svg>
                  <span class="emph">还没有任何记录</span>
                  <span>点击“开始记录”，它会静静看着、默默写下每一刻。</span>
                </div>
              </div>
              <div id="analysis" class="analysis" style="display:none">
                <div id="analysisSummary" class="analysis-summary"></div>
                <h3><span class="num">01</span>时间轴 · Gantt</h3>
                <div id="ganttWrap" class="gantt"></div>
                <h3><span class="num">02</span>分类占比 · Bar</h3>
                <div class="chart-wrap"><canvas id="categoryChart"></canvas></div>
                <h3><span class="num">03</span>活动明细 · Table</h3>
                <div style="overflow-x:auto"><table class="table" id="activityTable"><thead><tr><th>时段</th><th>标题</th><th>分类</th><th>时长</th><th>细节</th></tr></thead><tbody></tbody></table></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="grid-controls" id="hardware">
        <div class="card">
          <div class="card-head">
            <div class="tag"><svg><use href="#i-servo"/></svg></div>
            <div>
              <div class="title">舵机 & 音画</div>
              <div class="subtitle">Head · Ears · Volume · Brightness</div>
            </div>
          </div>
          <div class="card-body">
            <div class="section-title">四路 PWM 舵机测试</div>
            <div class="slider-row"><label>CH0 / Servo1</label><input id="rawServo0" type="range" min="0" max="180" value="90" oninput="updateRawServo(0,this.value)"><span class="val" id="rawServo0Val">90</span></div>
            <div class="slider-row"><label>CH1 / Servo2</label><input id="rawServo1" type="range" min="0" max="180" value="90" oninput="updateRawServo(1,this.value)"><span class="val" id="rawServo1Val">90</span></div>
            <div class="slider-row"><label>CH2 / Servo3</label><input id="rawServo2" type="range" min="0" max="180" value="90" oninput="updateRawServo(2,this.value)"><span class="val" id="rawServo2Val">90</span></div>
            <div class="slider-row"><label>CH3 / Servo4</label><input id="rawServo3" type="range" min="0" max="180" value="90" oninput="updateRawServo(3,this.value)"><span class="val" id="rawServo3Val">90</span></div>
            <div class="btn-row" style="margin-top:12px">
              <button class="btn sm" onclick="enableRawPwmTest()"><svg><use href="#i-servo"/></svg>进入测试</button>
              <button class="btn sm primary" onclick="centerRawPwmServos()"><svg><use href="#i-reset"/></svg>四路回中</button>
              <button class="btn sm" onclick="disableRawPwmTest()"><svg><use href="#i-stop"/></svg>退出测试</button>
            </div>
            <div class="hint">这里走最基础的 <code>50Hz PWM + 0-180°</code> 原始控制，不做头部限位、耳朵镜像，也不走表情动作队列，专门单测四个 <code>MG90S</code>。</div>
            <div class="section-title">姿态</div>
            <div class="slider-row"><label>俯仰 CH0</label><input id="pitch" type="range" min="70" max="96" value="90" oninput="updateServo('pitch',0,this.value)"><span class="val" id="pitchVal">90</span></div>
            <div class="slider-row"><label>左右 CH3</label><input id="yaw" type="range" min="50" max="130" value="90" oninput="updateServo('yaw',3,this.value)"><span class="val" id="yawVal">90</span></div>
            <div class="slider-row"><label>双耳镜像</label><input id="ear" type="range" min="0" max="180" value="90" oninput="updateEars(this.value)"><span class="val" id="earVal">90</span></div>
            <div class="btn-row" style="margin-top:12px">
              <button class="btn sm" onclick="sendRaw('CATPOSE:CLEAR')"><svg><use href="#i-clear"/></svg>清空姿态</button>
              <button class="btn sm" onclick="sendRaw('EXPR:listening')"><svg><use href="#i-mic"/></svg>倾听</button>
              <button class="btn sm primary" onclick="resetPose()"><svg><use href="#i-reset"/></svg>回中</button>
            </div>
            <div class="section-title">硬件</div>
            <div class="slider-row"><label><svg style="width:13px;height:13px;vertical-align:-2px;margin-right:4px"><use href="#i-volume"/></svg>音量</label><input id="audioVolume" type="range" min="0" max="100" value="78" oninput="updateAudioVolume(this.value)"><span class="val" id="audioVolumeVal">78</span></div>
            <div class="slider-row"><label><svg style="width:13px;height:13px;vertical-align:-2px;margin-right:4px"><use href="#i-brightness"/></svg>亮度</label><input id="screenBrightness" type="range" min="0" max="255" value="255" oninput="updateScreenBrightness(this.value)"><span class="val" id="screenBrightnessVal">255</span></div>
            <div class="hint">俯仰范围 <code>70 – 96</code>，左右走 <code>CH3</code>；双耳镜像使用 <code>CH1 / CH2</code>。</div>
          </div>
        </div>

        <div class="card">
          <div class="card-head">
            <div class="tag"><svg><use href="#i-actions"/></svg></div>
            <div>
              <div class="title">情绪 & 固件动作</div>
              <div class="subtitle">Expressions · Screens · Actions</div>
            </div>
          </div>
          <div class="card-body">
            <div class="section-title">情绪</div>
            <div class="chip-row">
              <span class="chip" onclick="sendRaw('EXPR:happy')"><span class="emo">😺</span>开心</span>
              <span class="chip" onclick="sendRaw('EXPR:sad')"><span class="emo">😿</span>难过</span>
              <span class="chip" onclick="sendRaw('EXPR:angry')"><span class="emo">😾</span>生气</span>
              <span class="chip" onclick="sendRaw('EXPR:shy')"><span class="emo">🙈</span>害羞</span>
              <span class="chip" onclick="sendRaw('EXPR:fear')"><span class="emo">🫣</span>害怕</span>
              <span class="chip" onclick="sendRaw('EXPR:thinking')"><span class="emo">💭</span>思考</span>
            </div>
            <div class="section-title">屏幕</div>
            <div class="btn-row">
              <button class="btn sm" onclick="setScreenMode(0)">本地表情</button>
              <button class="btn sm" onclick="setScreenMode(1)">状态面板</button>
              <button class="btn sm" onclick="setScreenMode(2)">主机相机</button>
              <button class="btn sm" onclick="setScreenMode(3)">同传屏</button>
              <button id="visionScreenBtn" class="btn sm ghost" onclick="toggleVisionScreen()"><svg><use href="#i-cam"/></svg><span id="visionScreenBtnText">启动图文排版屏</span></button>
            </div>
            <div class="hint">这个按钮只负责把 ESP32 屏幕切到图像+文字排版页；正常对话时即使停留在表情屏，也会按单帧把摄像头图像传给模型。</div>
            <div class="section-title">动作库</div>
            <div class="actions-grid">
              <button class="btn" onclick="playAction(1)"><span class="n">01</span>开心</button>
              <button class="btn" onclick="playAction(2)"><span class="n">02</span>低头</button>
              <button class="btn" onclick="playAction(3)"><span class="n">03</span>瞪视</button>
              <button class="btn" onclick="playAction(4)"><span class="n">04</span>害羞</button>
              <button class="btn" onclick="playAction(5)"><span class="n">05</span>害怕</button>
              <button class="btn" onclick="playAction(6)"><span class="n">06</span>思考</button>
              <button class="btn" onclick="playAction(7)"><span class="n">07</span>倾听</button>
              <button class="btn" onclick="playAction(8)"><span class="n">08</span>惊讶</button>
              <button class="btn" onclick="playAction(9)"><span class="n">09</span>无聊</button>
              <button class="btn" onclick="playAction(10)"><span class="n">10</span>沉默</button>
              <button class="btn" onclick="playAction(11)"><span class="n">11</span>呼吸</button>
              <button class="btn" onclick="playAction(12)"><span class="n">12</span>左探</button>
              <button class="btn" onclick="playAction(13)"><span class="n">13</span>右探</button>
              <button class="btn" onclick="playAction(14)"><span class="n">14</span>抖耳</button>
              <button class="btn" onclick="playAction(15)"><span class="n">15</span>抬头</button>
              <button class="btn" onclick="playAction(16)"><span class="n">16</span>摇摆</button>
              <button class="btn" onclick="playAction(17)"><span class="n">17</span>双倾</button>
              <button class="btn" onclick="playAction(18)"><span class="n">18</span>扫描</button>
              <button class="btn" onclick="playAction(19)"><span class="n">19</span>打盹</button>
              <button class="btn" onclick="playAction(20)"><span class="n">20</span>探查</button>
            </div>
            <div class="btn-row" style="margin-top:12px"><button class="btn danger sm" onclick="sendRaw('ACTION:STOP')"><svg><use href="#i-stop"/></svg>停止动作</button></div>
            <div class="hint"><code>1-10</code> 为表情动作，<code>11-20</code> 为 idle 动作。空闲时固件会按 60% 静止、40% 随机 idle 自动播放。</div>
          </div>
        </div>
      </div>

      <footer class="footer">
        <div class="copy">meow://console · build live</div>
        <div class="dots">
          <span id="footerCam"></span>
          <span id="footerUi"></span>
          <span id="footerRec"></span>
          <span>v2.1</span>
        </div>
      </footer>
    </div>
  </section>
</main>

<div id="lightbox" class="lightbox" onclick="if(event.target===this) this.classList.remove('on')">
  <div class="lightbox-close" onclick="document.getElementById('lightbox').classList.remove('on')"><svg><use href="#i-close"/></svg></div>
  <img id="lightboxImg" alt="">
</div>

<div id="toasts" class="toasts"></div>

<script>
let wsViewer = null;
let wsUi = null;
let recordingOn = false;
let entriesCache = [];
let categoryChart = null;
let lastFrameAt = 0;
let localTtsAudio = null;

/* ---- translation state ---- */
let translationPairs = [
  {pair:'zh-en', name:'中 ⇌ EN', display:'中英互译'},
];
let translationState = {active:false, pair:'zh-en', current:'', source:'', finalized:false};
let selectedTranslatePair = 'zh-en';

/* ---- performance mode（极速画面模式） ---- */
let performanceModeActive = false;
let rawPwmTestMode = false;
let visionScreenPinned = false;

const canvas = document.getElementById('videoCanvas');
const ctx = canvas.getContext('2d');
const camStatus = document.getElementById('camStatus');
const uiStatus = document.getElementById('uiStatus');
const recStatus = document.getElementById('recStatus');
const footerCam = document.getElementById('footerCam');
const footerUi = document.getElementById('footerUi');
const footerRec = document.getElementById('footerRec');
const chatBox = document.getElementById('chatBox');
const partialText = document.getElementById('partialText');
const timeline = document.getElementById('timeline');
const recToggleBtn = document.getElementById('recToggleBtn');
const recToggleText = document.getElementById('recToggleText');
const recToggleIcon = document.getElementById('recToggleIcon');
const recCountEl = document.getElementById('recCount');
const heroCount = document.getElementById('heroCount');
const heroSession = document.getElementById('heroSession');
const heroStatus = document.getElementById('heroStatus');
const heroInterval = document.getElementById('heroInterval');
const recSessionEl = document.getElementById('recSession');
const recStateEl = document.getElementById('recState');
const recIntervalEl = document.getElementById('recInterval');
const askInput = document.getElementById('askInput');
const analysisWrap = document.getElementById('analysis');
const visionTimer = document.getElementById('visionTimer');
const toastsEl = document.getElementById('toasts');

/* ---- utilities ---- */
function setStatusPill(el, cls, text){
  el.className = 'pill ' + cls;
  el.querySelector('span:last-child').textContent = text;
}
function setConn(el, dot, on, text){
  setStatusPill(el, on ? 'on' : 'off', text);
  dot.classList.toggle('on', !!on);
}
function setRecordPill(on, count){
  if(on){
    setStatusPill(recStatus, 'rec', '记录中 · ' + (count||0) + ' 张');
    footerRec.classList.add('on');
  } else {
    setStatusPill(recStatus, '', '未在记录');
    footerRec.classList.remove('on');
  }
}

function toast(kind, text){
  const el = document.createElement('div');
  el.className = 'toast ' + (kind||'info');
  const ic = kind==='success' ? 'i-check' : kind==='error' ? 'i-alert' : 'i-info';
  el.innerHTML = '<svg class="ic" style="width:16px;height:16px"><use href="#'+ic+'"/></svg><div class="body"></div>';
  el.querySelector('.body').textContent = text;
  toastsEl.appendChild(el);
  setTimeout(()=>{ el.style.transition='all .3s ease'; el.style.opacity='0'; el.style.transform='translateX(20px)'; }, 2600);
  setTimeout(()=>el.remove(), 3000);
}

function startLocalTtsAudio(src){
  stopLocalTtsAudio();
  localTtsAudio = new Audio(src || ('/stream.wav?local=' + Date.now()));
  localTtsAudio.autoplay = true;
  localTtsAudio.preload = 'auto';
  localTtsAudio.play().catch(() => {
    toast('error', '浏览器拦截了本地播放，请再点一次复述按钮');
  });
}

function stopLocalTtsAudio(){
  if(!localTtsAudio) return;
  try { localTtsAudio.pause(); } catch(e) {}
  localTtsAudio.removeAttribute('src');
  try { localTtsAudio.load(); } catch(e) {}
  localTtsAudio = null;
}

function addMessage(text, kind){
  const row = document.createElement('div');
  row.className = 'chat-line ' + (kind || '');
  const who = document.createElement('div');
  who.className = 'who';
  who.textContent = kind === 'ai' ? 'AI' : (kind === 'rec' ? 'REC' : 'YOU');
  const msg = document.createElement('div');
  msg.className = 'msg';
  msg.textContent = text;
  row.appendChild(who);
  row.appendChild(msg);
  chatBox.insertBefore(row, partialText);
  chatBox.scrollTop = chatBox.scrollHeight;
}

/* ---- camera ---- */
function connectCamera(){
  if(wsViewer) wsViewer.close();
  const host = location.host || 'localhost:8081';
  wsViewer = new WebSocket('ws://' + host + '/ws/viewer');
  wsViewer.binaryType = 'arraybuffer';
  wsViewer.onopen = () => { setConn(camStatus, footerCam, true, '画面已连'); };
  wsViewer.onclose = () => { setConn(camStatus, footerCam, false, '画面未连'); };
  wsViewer.onerror = () => { setConn(camStatus, footerCam, false, '画面错误'); };
  wsViewer.onmessage = (e) => {
    if(!(e.data instanceof ArrayBuffer)) return;
    const blob = new Blob([e.data], {type:'image/jpeg'});
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
      ctx.clearRect(0,0,canvas.width,canvas.height);
      ctx.drawImage(img,0,0,canvas.width,canvas.height);
      URL.revokeObjectURL(url);
      lastFrameAt = Date.now();
    };
    img.src = url;
  };
}
function disconnectCamera(){ if(wsViewer) wsViewer.close(); }

setInterval(()=>{
  if(!lastFrameAt) { visionTimer.textContent = '—'; return; }
  const secs = Math.floor((Date.now()-lastFrameAt)/1000);
  visionTimer.textContent = secs < 2 ? 'LIVE' : (secs + 's ago');
}, 500);

/* ---- UI WS ---- */
function connectUI(){
  if(wsUi) wsUi.close();
  const host = location.host || 'localhost:8081';
  wsUi = new WebSocket('ws://' + host + '/ws_ui');
  wsUi.onopen = () => { setConn(uiStatus, footerUi, true, '控制已连'); };
  wsUi.onclose = () => { setConn(uiStatus, footerUi, false, '控制未连'); };
  wsUi.onerror = () => { setConn(uiStatus, footerUi, false, '控制错误'); };
  wsUi.onmessage = (e) => handleUiMessage(e.data);
}
function disconnectUI(){ if(wsUi) wsUi.close(); }

function handleUiMessage(data){
  if(data.startsWith('PARTIAL:')){
    partialText.textContent = data.substring(8) || '...';
  } else if(data.startsWith('FINAL:')){
    const text = data.substring(6);
    const kind = text.startsWith('[记录答复]') ? 'rec' :
                 ((text.startsWith('[AI]') || text.startsWith('[复述]') || text.startsWith('[译文]') || text.startsWith('[原文]')) ? 'ai' : '');
    addMessage(text, kind);
    partialText.textContent = '';
  } else if(data.startsWith('INIT:')){
    const init = JSON.parse(data.substring(5));
    (init.finals || []).forEach((item) => {
      const kind = item.startsWith('[记录答复]') ? 'rec' :
                   ((item.startsWith('[AI]')||item.startsWith('[复述]')||item.startsWith('[译文]')||item.startsWith('[原文]')) ? 'ai' : '');
      addMessage(item, kind);
    });
    if(init.partial) partialText.textContent = init.partial;
    if(Array.isArray(init.translation_pairs) && init.translation_pairs.length){
      translationPairs = init.translation_pairs;
      renderTranslatePairs();
    }
    if(init.performance_mode){
      applyPerformanceMode(init.performance_mode);
    }
    if(init.vision_screen){
      applyVisionScreenState(init.vision_screen);
    }
  } else if(data.startsWith('RECORD:')){
    try { handleRecordEvent(JSON.parse(data.substring(7))); } catch(e){}
  } else if(data.startsWith('TRANSLATE:')){
    try { applyTranslationState(JSON.parse(data.substring(10))); } catch(e){}
  } else if(data.startsWith('PERFMODE:')){
    try { applyPerformanceMode(JSON.parse(data.substring(9))); } catch(e){}
  } else if(data.startsWith('VISPIN:')){
    try { applyVisionScreenState(JSON.parse(data.substring(7))); } catch(e){}
  } else if(data.startsWith('LOCALAUDIO:PLAY:')){
    startLocalTtsAudio(data.substring(16));
  } else if(data === 'LOCALAUDIO:START'){
    startLocalTtsAudio();
  } else if(data === 'LOCALAUDIO:STOP'){
    stopLocalTtsAudio();
  }
}

/* ---- performance mode helpers ---- */
function applyPerformanceMode(state){
  const active = !!(state && state.active);
  performanceModeActive = active;
  const btn = document.getElementById('perfBtn');
  const btnText = document.getElementById('perfBtnText');
  const banner = document.getElementById('perfBanner');
  if(btn){ btn.classList.toggle('on', active); }
  if(btnText){ btnText.textContent = active ? '关闭极速' : '极速画面'; }
  if(banner){ banner.classList.toggle('on', active); }
  document.body.classList.toggle('perf-on', active);
}
function setPerformanceMode(on){
  const msg = 'PERFMODE:' + (on ? 'ON' : 'OFF');
  if(wsUi && wsUi.readyState === 1){
    wsUi.send(msg);
  } else {
    // 没连上控制 WS 时，通过 HTTP 兜底切换
    fetch('/performance_mode?active=' + (on?1:0), {method:'POST'})
      .then(r=>r.json()).then(j=>applyPerformanceMode(j))
      .catch(()=>toast('error','切换失败，请先连接控制 WS'));
  }
  if(on) toast('info','极速画面模式开启，其它功能暂停');
  else   toast('success','已退出极速画面模式');
}
function togglePerformanceMode(){
  setPerformanceMode(!performanceModeActive);
}

function applyVisionScreenState(state){
  visionScreenPinned = !!(state && state.pinned);
  const btn = document.getElementById('visionScreenBtn');
  const text = document.getElementById('visionScreenBtnText');
  if(btn){
    btn.classList.toggle('primary', visionScreenPinned);
    btn.classList.toggle('ghost', !visionScreenPinned);
  }
  if(text){
    text.textContent = visionScreenPinned ? '关闭图文排版屏' : '启动图文排版屏';
  }
}

function toggleVisionScreen(){
  if(wsUi && wsUi.readyState === 1){
    wsUi.send('VISIONSCREEN:' + (visionScreenPinned ? 'OFF' : 'ON'));
    return;
  }
  toast('error', '请先连接控制通道');
}

function sendRaw(message){ if(wsUi && wsUi.readyState === 1) wsUi.send(message); }
function sendPrompt(){
  const text = document.getElementById('promptText').value.trim();
  if(!text) return;
  sendRaw('PROMPT:' + text);
  document.getElementById('promptText').value = '';
}
function sendRepeat(){
  const text = document.getElementById('repeatText').value.trim();
  if(!text) return;
  sendRaw('REPEAT:' + text);
}

/* ---- servo / hardware ---- */
function setRawServoSlider(channel, value){
  const safeChannel = Math.max(0, Math.min(3, Number(channel)));
  const input = document.getElementById('rawServo' + safeChannel);
  const safe = Math.max(0, Math.min(180, Number(value)));
  input.value = safe;
  document.getElementById('rawServo' + safeChannel + 'Val').textContent = safe;
}
function enableRawPwmTest(){
  rawPwmTestMode = true;
  sendRaw('PWMTEST:ON');
  toast('info', '已进入四路 PWM 原始测试模式');
}
function disableRawPwmTest(){
  rawPwmTestMode = false;
  sendRaw('PWMTEST:OFF');
  toast('success', '已退出四路 PWM 原始测试模式');
}
function centerRawPwmServos(){
  if(!rawPwmTestMode){
    sendRaw('PWMTEST:ON');
    rawPwmTestMode = true;
  }
  [0,1,2,3].forEach((channel)=>{
    setRawServoSlider(channel, 90);
    sendRaw('PWMRAW:' + channel + ',90');
  });
}
function updateRawServo(channel, value){
  const safeChannel = Math.max(0, Math.min(3, Number(channel)));
  const safe = Math.max(0, Math.min(180, Number(value)));
  setRawServoSlider(safeChannel, safe);
  if(!rawPwmTestMode){
    sendRaw('PWMTEST:ON');
    rawPwmTestMode = true;
  }
  sendRaw('PWMRAW:' + safeChannel + ',' + safe);
}
function updateServo(prefix, channel, value){
  const input = document.getElementById(prefix);
  const min = Number(input.min), max = Number(input.max);
  const safe = Math.max(min, Math.min(max, Number(value)));
  input.value = safe;
  document.getElementById(prefix + 'Val').textContent = safe;
  sendRaw('SERVO:' + channel + ',' + safe);
}
function updateEars(value){ document.getElementById('earVal').textContent = value; sendRaw('EARS:' + value); }
function resetPose(){
  ['yaw','pitch','ear'].forEach(k=>{ document.getElementById(k).value = 90; document.getElementById(k+'Val').textContent = 90; });
  sendRaw('CATPOSE:CLEAR'); sendRaw('CATPOSE:90,90,90,160');
}
function setScreenMode(mode){ sendRaw('SCRMODE:' + mode); }
function playAction(id){ sendRaw('ACTION:' + id); }
function updateAudioVolume(value){
  const v = Math.max(0, Math.min(100, Number(value)));
  document.getElementById('audioVolume').value = v;
  document.getElementById('audioVolumeVal').textContent = v;
  sendRaw('AUDIOSET:volume,' + v);
}
function updateScreenBrightness(value){
  const v = Math.max(0, Math.min(255, Number(value)));
  document.getElementById('screenBrightness').value = v;
  document.getElementById('screenBrightnessVal').textContent = v;
  sendRaw('TFTSET:brightness,' + v);
}

/* ---- recording ---- */
function toggleRecording(){
  const url = recordingOn ? '/record/stop' : '/record/start';
  recToggleBtn.disabled = true;
  fetch(url, {method:'POST'})
    .then(r=>r.json())
    .then(data=>{
      if(data && data.state){ applyRecordState(data.state); }
      toast('success', recordingOn ? '已开始记录' : '已停止记录');
    })
    .catch(err=>toast('error','切换失败：' + err))
    .finally(()=>{ recToggleBtn.disabled = false; });
}

function applyRecordState(state){
  recordingOn = !!state.active;
  recToggleText.textContent = recordingOn ? '停止记录' : '开始记录';
  recToggleIcon.innerHTML = recordingOn ? '<use href="#i-stop"/>' : '<use href="#i-play"/>';
  recToggleBtn.classList.toggle('on', recordingOn);
  recSessionEl.textContent = state.session_id || '—';
  heroSession.textContent = state.session_id || '—';
  recCountEl.textContent = state.count || 0;
  heroCount.textContent = state.count || 0;
  recIntervalEl.textContent = Math.round(state.interval_sec || 10);
  heroInterval.textContent = Math.round(state.interval_sec || 10);
  recStateEl.textContent = state.analysis_in_flight ? '分析中' : (recordingOn ? '记录中' : '待命');
  heroStatus.textContent = state.analysis_in_flight ? '分析中' : (recordingOn ? '记录中' : '待命');
  setRecordPill(recordingOn, state.count || 0);
}

function renderTimeline(){
  if(!entriesCache.length){
    timeline.innerHTML = '<div class="empty"><svg><use href="#i-empty"/></svg><span class="emph">还没有任何记录</span><span>点击“开始记录”，它会静静看着、默默写下每一刻。</span></div>';
    return;
  }
  timeline.innerHTML = '';
  const reversed = entriesCache.slice().reverse();
  reversed.forEach((entry, i)=>{
    const row = document.createElement('div');
    row.className = 'entry';
    const idx = entriesCache.length - i;
    row.innerHTML = [
      '<div class="thumb"><img alt="" loading="lazy"></div>',
      '<div class="meta"><div class="time">'+ (entry.time_label || '') +'</div><div class="desc"></div></div>',
      '<div class="idx">#'+ String(idx).padStart(3,'0') +'</div>'
    ].join('');
    const img = row.querySelector('img');
    img.src = entry.image_url;
    img.alt = entry.description || '';
    row.querySelector('.thumb').addEventListener('click', ()=>{
      document.getElementById('lightboxImg').src = entry.image_url;
      document.getElementById('lightbox').classList.add('on');
    });
    row.querySelector('.desc').textContent = entry.description || '(描述生成中)';
    timeline.appendChild(row);
  });
}

function handleRecordEvent(evt){
  if(!evt || !evt.kind) return;
  if(evt.kind === 'started'){
    entriesCache = [];
    renderTimeline();
    if(evt.payload && evt.payload.state) applyRecordState(evt.payload.state);
    toast('info','开始记录 · ' + (evt.payload && evt.payload.state && evt.payload.state.session_id || ''));
  } else if(evt.kind === 'stopped'){
    if(evt.payload && evt.payload.state) applyRecordState(evt.payload.state);
    toast('info','记录已停止');
  } else if(evt.kind === 'entry'){
    entriesCache.push(evt.payload);
    recCountEl.textContent = entriesCache.length;
    heroCount.textContent = entriesCache.length;
    setRecordPill(recordingOn, entriesCache.length);
    renderTimeline();
  } else if(evt.kind === 'analysis_started'){
    recStateEl.textContent = '分析中'; heroStatus.textContent='分析中';
    toast('info','AI 正在分析记录…');
  } else if(evt.kind === 'analysis_result'){
    recStateEl.textContent = recordingOn ? '记录中' : '待命';
    heroStatus.textContent = recordingOn ? '记录中' : '待命';
    renderAnalysis(evt.payload || {});
    toast('success','分析完成');
  }
}

function askRecord(){
  const q = askInput.value.trim();
  if(!q) return;
  addMessage('[记录问] ' + q, 'rec');
  askInput.value = '';
  fetch('/record/ask', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({question:q})})
    .then(r=>r.json())
    .then(data=>{ if(data && data.answer) addMessage('[记录答复] ' + data.answer, 'rec'); })
    .catch(err=>addMessage('[记录答复] 提问失败：' + err, 'rec'));
}

function runAnalysis(){
  if(!entriesCache.length){ toast('error','还没有记录可供分析'); return; }
  recStateEl.textContent = '分析中';
  heroStatus.textContent = '分析中';
  fetch('/record/analyze', {method:'POST'})
    .then(r=>r.json())
    .then(data=>{
      recStateEl.textContent = recordingOn ? '记录中' : '待命';
      heroStatus.textContent = recordingOn ? '记录中' : '待命';
      if(data && data.result) renderAnalysis(data.result);
    })
    .catch(err=>{
      recStateEl.textContent = recordingOn ? '记录中' : '待命';
      heroStatus.textContent = recordingOn ? '记录中' : '待命';
      toast('error','分析失败：' + err);
    });
}

function minutesToMs(str){
  if(!str || typeof str !== 'string') return null;
  const m = str.match(/^(\d{1,2}):(\d{2})/);
  if(!m) return null;
  return (Number(m[1])*60 + Number(m[2])) * 60 * 1000;
}

function renderAnalysis(result){
  analysisWrap.style.display = 'block';
  analysisWrap.scrollIntoView({behavior:'smooth', block:'nearest'});
  document.getElementById('analysisSummary').textContent = result.summary || '（暂无总结）';

  const ganttWrap = document.getElementById('ganttWrap');
  ganttWrap.innerHTML = '';
  const activities = Array.isArray(result.activities) ? result.activities : [];
  if(activities.length){
    const starts = activities.map(a=>minutesToMs(a.start)).filter(v=>v!==null);
    const ends = activities.map(a=>minutesToMs(a.end)).filter(v=>v!==null);
    const minT = Math.min.apply(null, starts.length ? starts : [0]);
    const maxT = Math.max.apply(null, ends.length ? ends : [1]);
    const span = Math.max(1, maxT - minT);
    activities.forEach(a=>{
      const row = document.createElement('div'); row.className='gantt-row';
      const lbl = document.createElement('div'); lbl.className='lbl';
      lbl.textContent = (a.start||'—') + ' → ' + (a.end||'—');
      const track = document.createElement('div'); track.className='gantt-track';
      const s = minutesToMs(a.start); const e = minutesToMs(a.end);
      const bar = document.createElement('div'); bar.className='gantt-bar';
      if(s!==null && e!==null && e>s){
        bar.style.left = ((s-minT)/span*100) + '%';
        bar.style.width = Math.max(4, (e-s)/span*100) + '%';
      } else {
        bar.style.left='0'; bar.style.width='6%';
      }
      bar.textContent = (a.title||'') + (a.category?(' · '+a.category):'');
      track.appendChild(bar); row.appendChild(lbl); row.appendChild(track);
      ganttWrap.appendChild(row);
    });
  } else {
    ganttWrap.innerHTML = '<div class="empty" style="padding:18px">模型没有提取出可视化的时间段。</div>';
  }

  const cats = Array.isArray(result.categories) ? result.categories : [];
  const ctx2 = document.getElementById('categoryChart').getContext('2d');
  if(categoryChart){ categoryChart.destroy(); categoryChart = null; }
  Chart.defaults.font.family = "'Inter',sans-serif";
  categoryChart = new Chart(ctx2, {
    type:'bar',
    data:{
      labels: cats.map(c=>c.name||''),
      datasets:[{
        label:'分钟',
        data: cats.map(c=>Number(c.minutes)||0),
        backgroundColor:(c)=>{
          const chart = c.chart; const {ctx,chartArea} = chart;
          if(!chartArea) return 'rgba(124,140,255,.55)';
          const g = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
          g.addColorStop(0,'rgba(124,140,255,.15)'); g.addColorStop(1,'rgba(197,107,255,.9)');
          return g;
        },
        borderColor:'rgba(197,107,255,.7)', borderWidth:1, borderRadius:8, maxBarThickness:44
      }]
    },
    options:{
      responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false}, tooltip:{backgroundColor:'rgba(10,12,18,.95)',borderColor:'rgba(255,255,255,.1)',borderWidth:1,padding:10}},
      scales:{
        x:{ticks:{color:'#8a93a7',font:{size:11}},grid:{color:'rgba(255,255,255,.04)'}},
        y:{beginAtZero:true, ticks:{color:'#8a93a7',font:{size:11}},grid:{color:'rgba(255,255,255,.04)'}}
      }
    }
  });

  const tbody = document.querySelector('#activityTable tbody');
  tbody.innerHTML = '';
  activities.forEach(a=>{
    const tr = document.createElement('tr');
    tr.innerHTML = '<td class="t"></td><td></td><td class="cat"></td><td class="min"></td><td></td>';
    const cells = tr.querySelectorAll('td');
    cells[0].textContent = (a.start||'—') + ' – ' + (a.end||'—');
    cells[1].textContent = a.title || '';
    cells[2].textContent = a.category || '';
    cells[3].textContent = (a.duration_minutes!=null? a.duration_minutes : '');
    cells[4].textContent = a.detail || '';
    tbody.appendChild(tr);
  });
}

/* ---- translation ---- */
function renderTranslatePairs(){
  const host = document.getElementById('trPairs');
  if(!host) return;
  host.innerHTML = '';
  translationPairs.forEach(item => {
    const el = document.createElement('div');
    el.className = 'tr-pair' + (item.pair === selectedTranslatePair ? ' active' : '');
    el.innerHTML = '<span style="font-family:var(--mono);color:#9ec7ff">' + (item.name || item.pair) + '</span><span class="lbl">' + (item.display || '') + '</span>';
    el.addEventListener('click', () => {
      selectedTranslatePair = item.pair;
      renderTranslatePairs();
      if(translationState.active){
        startTranslate();
      }
    });
    host.appendChild(el);
  });
  const badge = document.getElementById('trPairBadge');
  if(badge){
    const current = translationPairs.find(p => p.pair === (translationState.pair || selectedTranslatePair));
    badge.textContent = current ? (current.name || current.pair) : '';
  }
}

function startTranslate(){
  sendRaw('TRANSLATE_START:' + (selectedTranslatePair || 'zh-en'));
}
function stopTranslate(){
  sendRaw('TRANSLATE_STOP');
}

function applyTranslationState(state){
  translationState = Object.assign({active:false, pair:'zh-en', current:'', source:'', finalized:false}, state || {});
  selectedTranslatePair = translationState.pair || selectedTranslatePair;
  const stateEl = document.getElementById('trState');
  const stateTxt = document.getElementById('trStateText');
  const translated = document.getElementById('trTranslated');
  const source = document.getElementById('trSource');
  const startBtn = document.getElementById('trStartBtn');
  const stopBtn = document.getElementById('trStopBtn');
  if(stateEl) stateEl.classList.toggle('on', !!translationState.active);
  if(stateTxt) stateTxt.textContent = translationState.active ? ('已启用 · ' + (translationState.display || translationState.pair || '')) : '未激活';
  if(startBtn) startBtn.textContent = translationState.active ? '重新开始同传' : '开始同传';
  if(stopBtn) stopBtn.disabled = !translationState.active;
  if(translated){
    if(translationState.current){
      const cursor = translationState.finalized ? '' : '<span class="cursor"></span>';
      translated.innerHTML = '';
      const span = document.createElement('span');
      span.textContent = translationState.current;
      translated.appendChild(span);
      if(!translationState.finalized){
        const c = document.createElement('span');
        c.className = 'cursor';
        translated.appendChild(c);
      }
    } else {
      translated.innerHTML = '<span style="color:var(--fg-dim)">' + (translationState.active ? '正在聆听…' : '等待翻译…') + '</span>';
    }
  }
  if(source){
    if(translationState.source){
      source.textContent = translationState.source;
    } else {
      source.innerHTML = '<span style="color:var(--fg-muted)">（原文将出现在这里）</span>';
    }
  }
  renderTranslatePairs();
}

function fetchInitialRecordState(){
  fetch('/record/state').then(r=>r.json()).then(data=>{
    if(data && data.state){ applyRecordState(data.state); }
    if(data && Array.isArray(data.entries)){ entriesCache = data.entries; renderTimeline(); }
  }).catch(()=>{});
}

/* ---- nav active link ---- */
document.querySelectorAll('.nav-links a').forEach(a=>{
  a.addEventListener('click', ()=>{
    document.querySelectorAll('.nav-links a').forEach(x=>x.classList.remove('active'));
    a.classList.add('active');
  });
});

renderTranslatePairs();
applyTranslationState({active:false, pair:selectedTranslatePair, current:'', source:'', finalized:false});

setTimeout(()=>{ connectUI(); fetchInitialRecordState(); }, 400);
</script>
</body>
</html>"""
