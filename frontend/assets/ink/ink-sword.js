/**
 * 墨参 MoShen · 动画1「笔墨化剑」
 * ---------------------------------------------------------------
 * 依据《加载与等待动画需求文档》§2 与《加载动画设计规范文档》§3 实现。
 *
 * 叙事：一笔、一斩、一痕。
 *   1 落笔起势 0.00–0.35s   笔自右上入画，笔尖落点晕开墨点
 *   2 行笔成弧 0.35–1.30s   笔沿 S 形路径运笔，墨迹随笔尖生长
 *   3 提笔化剑 1.30–1.75s   提笔上扬，墨烟横掠遮挡，笔收束化剑
 *   4 横斩留痕 1.75–2.15s   剑自左向右掠过，斩痕延迟 0.06s 拖出
 *   5 定格     2.15–2.45s   只留「一道弧 + 一道横斩」
 *   6 消散回接 2.45–2.90s   墨痕自两端向中间溶解，回到首帧状态
 *
 * 实现要点：
 *   · 全部时间轴用 SMIL，单一时钟 dur=2.9s 无限循环，循环点在数学上闭合
 *   · 墨迹/斩痕直接使用美术签名帧拆分出的图层，定格帧与美术稿完全一致
 *   · 双主题不是两套动画：深色层是按深色配色重新着色的同形素材，切换只切显示
 *   · 行笔顺序由美术稿墨迹中线（测地距离法）拟合出的 S 路径决定，墨迹沿路径"生长"
 *   · 降级：prefers-reduced-motion → 停在定格帧并做 2.5s 呼吸；素材失败 → 圆环兜底；
 *          低性能 → 自动切定格帧；页面不可见 → 暂停且不重置进度
 */
(function (global) {
  'use strict';

  var DUR = 2.9;
  var uid = 0;
  var FREEZE_T = 2.26;          // 定格帧时刻（§2.2 阶段5 中段，斩痕已完整、尚未消散）
  var PERF_FRAMES = 90;
  var PERF_THRESHOLD_MS = 42;   // ≈ 24fps

  // S 形路径：由 arc 图层墨迹中线按测地距离拟合出的 3 段三次贝塞尔
  // 起点 (572,184) 画面中上偏右（笔从这里入画）→ 终点 (388,813) 左下方（收笔处）
  var ARC_PATH =
    'M572,184 C489.6,249.8 292.5,281.1 324.7,381.5 ' +
    'C371.7,527.8 692,475.6 714.3,627.5 C732.5,751.4 496.8,751.3 388,813.2';

  // 斩击轴：由剑光层主轴测得，左端 (76,583) → 右端 (948,436)
  var BLADE_TILT = -9.61;
  var SWEEP_FROM = '150 585';
  var SWEEP_TO = '965 428';

  // 毛笔笔尖贴图：311×384，笔尖位于贴图左下角 (3,381)，质心→笔尖方向 135.89°
  var TIP_W = 311;
  var TIP_H = 384;
  var BRUSH_K = 0.55;                       // 相对美术原尺寸的缩放
  var BRUSH_W = +(TIP_W * 1.8125 * BRUSH_K).toFixed(1);   // 1.8125 = 1024/696（还原到 1024 逻辑画布）
  var BRUSH_H = +(TIP_H * 1.8125 * BRUSH_K).toFixed(1);
  var TIP_ROTATE = -135.89;                 // 把笔尖朝向摆到 +x
  var TIP_X = +(-3 * 1.8125 * BRUSH_K).toFixed(1);        // 让笔尖正好落在原点
  var TIP_Y = +(-381 * 1.8125 * BRUSH_K).toFixed(1);
  var BLADE_TX = 'rotate(' + BLADE_TILT + ') scale(1.65 0.22) rotate(' + TIP_ROTATE + ')';

  var MIST_Y = 620;

  function kt(times) {
    return times
      .map(function (t) {
        return (t / DUR).toFixed(5);
      })
      .join(';');
  }

  /* ------------------------------------------------------------------ */
  /* 标记                                                               */
  /* ------------------------------------------------------------------ */

  function tipImages(p) {
    return (
      '<image class="ink-l" href="' + p + 'brush-tip.png" x="' + TIP_X + '" y="' + TIP_Y + '" width="' + BRUSH_W + '" height="' + BRUSH_H + '"/>' +
      '<image class="ink-d" href="' + p + 'brush-tip-dark.png" x="' + TIP_X + '" y="' + TIP_Y + '" width="' + BRUSH_W + '" height="' + BRUSH_H + '"/>'
    );
  }

  function buildSvg(base) {
    var p = base.replace(/\/?$/, '/');
    // 同一页面可能同时存在多个实例（启动页 + 等待层），所有 id 必须带实例前缀
    var u = 'iks' + ++uid + '-';
    var id = function (n) { return u + n; };
    var ref = function (n) { return 'url(#' + u + n + ')'; };

    return (
      '<svg class="ink-sword" viewBox="0 0 1024 1024" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">' +
      '<defs>' +
      '<linearGradient id="' + id('fadeOutR') + '"><stop offset="0" stop-color="#000"/><stop offset="0.75" stop-color="#000"/><stop offset="1" stop-color="#fff"/></linearGradient>' +
      '<linearGradient id="' + id('fadeOutL') + '"><stop offset="0" stop-color="#fff"/><stop offset="0.25" stop-color="#000"/><stop offset="1" stop-color="#000"/></linearGradient>' +
      '<linearGradient id="' + id('cutGrad') + '"><stop offset="0" stop-color="#fff"/><stop offset="0.94" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>' +

      /* 行笔揭示：白色描边沿 S 路径生长，把墨迹按笔画顺序"擦"出来 */
      '<mask id="' + id('reveal') + '" maskUnits="userSpaceOnUse" x="-300" y="-300" width="1624" height="1624">' +
      '<path d="' + ARC_PATH + '" fill="none" stroke="#fff" stroke-width="384" stroke-linecap="round" stroke-linejoin="round"' +
      ' pathLength="1000" stroke-dasharray="1000" stroke-dashoffset="1000">' +
      '<animate attributeName="stroke-dashoffset" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.35, 0.52, 0.75, 1.02, 1.30, DUR]) + '"' +
      ' values="1000;1000;800;420;130;0;0"/></path>' +
      /* 收尾补形：末段把描边管未覆盖的墨点一并带出 */
      '<rect x="0" y="0" width="1024" height="1024" fill="#fff" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.12, 1.30, DUR]) + '" values="0;0;1;1"/></rect>' +
      '</mask>' +

      /* 斩痕揭示：自左向右拉开，软边由渐变末段承担（零滤镜开销） */
      '<mask id="' + id('cut') + '" maskUnits="userSpaceOnUse" x="-320" y="-320" width="1664" height="1664">' +
      '<rect x="-300" y="-200" width="350" height="1424" fill="' + ref('cutGrad') + '">' +
      '<animate attributeName="width" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.82, 2.12, DUR]) + '" values="350;350;1340;1340"/></rect>' +
      '</mask>' +

      /* 消散揭示：底白 + 两端向内生长，实现"自两端向中间溶解" */
      '<mask id="' + id('dissolve') + '" maskUnits="userSpaceOnUse" x="-40" y="-140" width="1104" height="1304">' +
      '<rect x="0" y="0" width="1024" height="1024" fill="#fff"/>' +
      '<rect x="0" y="-120" width="0" height="1264" fill="' + ref('fadeOutR') + '">' +
      '<animate attributeName="width" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 2.45, 2.88, DUR]) + '" values="0;0;720;720"/></rect>' +
      '<rect x="1024" y="-120" width="0" height="1264" fill="' + ref('fadeOutL') + '">' +
      '<animate attributeName="x" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 2.45, 2.88, DUR]) + '" values="1024;1024;304;304"/>' +
      '<animate attributeName="width" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 2.45, 2.88, DUR]) + '" values="0;0;720;720"/></rect>' +
      '</mask>' +
      '</defs>' +

      /* ===== 墨迹 + 斩痕（共用一层消散遮罩） ===== */
      '<g mask="' + ref('dissolve') + '">' +
      '<g mask="' + ref('reveal') + '">' +
      '<image class="ink-l" href="' + p + 'arc-light.png" x="0" y="0" width="1024" height="1024"/>' +
      '<image class="ink-d" href="' + p + 'arc-dark.png" x="0" y="0" width="1024" height="1024"/>' +
      '</g>' +
      '<g mask="' + ref('cut') + '">' +
      '<image class="ink-l" href="' + p + 'slash-light.png" x="0" y="0" width="1024" height="1024"/>' +
      '<image class="ink-d" href="' + p + 'slash-dark.png" x="0" y="0" width="1024" height="1024"/>' +
      '</g>' +
      '</g>' +

      /* ===== 落笔墨点（阶段1） ===== */
      '<g class="ink-blob" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.10, 0.55, 0.90, DUR]) + '" values="0;0.92;0.78;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.62, DUR]) + '" values="572 184;524 268;524 268"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="scale" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.24, 0.62, DUR]) + '" values="0.28;1.02;1.24;1.24"/>' +
      '<image class="ink-l" href="' + p + 'ink-blob.png" x="-100" y="-90" width="200" height="180"/>' +
      '<image class="ink-d" href="' + p + 'ink-blob-dark.png" x="-100" y="-90" width="200" height="180"/>' +
      '</g></g></g>' +

      '<g class="ink-splash" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.16, 0.46, 0.82, DUR]) + '" values="0;0.9;0.7;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.55, DUR]) + '" values="498 318;474 350;474 350"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="scale" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.30, DUR]) + '" values="0.25;1;1"/>' +
      '<image class="ink-l" href="' + p + 'ink-splash-a.png" x="-49" y="-53" width="98" height="107"/>' +
      '<image class="ink-d" href="' + p + 'ink-splash-a-dark.png" x="-49" y="-53" width="98" height="107"/>' +
      '</g></g></g>' +

      '<g class="ink-splash2" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.20, 0.50, 0.82, DUR]) + '" values="0;0.85;0.6;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.55, DUR]) + '" values="672 108;700 132;700 132"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="scale" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.30, DUR]) + '" values="0.25;1;1"/>' +
      '<image class="ink-l" href="' + p + 'ink-splash-b.png" x="-35" y="-31" width="70" height="63"/>' +
      '<image class="ink-d" href="' + p + 'ink-splash-b-dark.png" x="-35" y="-31" width="70" height="63"/>' +
      '</g></g></g>' +

      /* ===== 毛笔（阶段1-3） =====
       * A 入画/提笔位移（父坐标系，不受笔朝向影响）
       * B animateMotion（沿 S 路径 + rotate=auto 跟随切线）
       * C 笔锋上扬（在笔自身朝向坐标系内绕笔尖旋转）
       * D 缩放（绕笔尖） */
      '<g class="ink-brush" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.07, 1.38, 1.47, DUR]) + '" values="0;1;1;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.35, 1.30, 1.47, DUR]) + '"' +
      ' values="400 -410;0 0;0 0;-250 -175;-250 -175"/>' +
      '<g>' +
      '<animateMotion dur="' + DUR + 's" repeatCount="indefinite" rotate="auto" calcMode="linear"' +
      ' path="' + ARC_PATH + '"' +
      ' keyTimes="' + kt([0, 0.35, 0.52, 0.75, 1.02, 1.30, DUR]) + '"' +
      ' keyPoints="0;0;0.20;0.58;0.87;1;1"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="rotate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.35, 1.30, 1.47, DUR]) + '"' +
      ' values="-16;0;0;-38;-38"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="scale" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 0.35, 1.30, 1.47, DUR]) + '"' +
      ' values="0.82;1;1;0.72;0.72"/>' +
      '<g transform="rotate(' + TIP_ROTATE + ')">' + tipImages(p) + '</g>' +
      '</g></g></g></g>' +

      /* ===== 剑身（阶段3-4） ===== */
      '<g class="ink-blade" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.42, 1.47, 2.06, 2.18, DUR]) + '" values="0;0;1;1;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.47, 1.76, 2.04, DUR]) + '"' +
      ' values="' + SWEEP_FROM + ';' + SWEEP_FROM + ';' + SWEEP_FROM + ';' + SWEEP_TO + ';' + SWEEP_TO + '"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="scale" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.44, 1.66, DUR]) + '"' +
      ' values="0.45;0.45;1;1"/>' +
      '<g transform="' + BLADE_TX + '">' + tipImages(p) + '</g>' +
      '</g></g></g>' +

      /* ===== 剑气辉光（阶段4） ===== */
      '<g class="ink-glow" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.78, 1.86, 2.04, 2.20, DUR]) + '" values="0;0;1;0.85;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.78, 2.04, DUR]) + '"' +
      ' values="' + SWEEP_FROM + ';' + SWEEP_FROM + ';' + SWEEP_TO + ';' + SWEEP_TO + '"/>' +
      '<g transform="rotate(' + BLADE_TILT + ') translate(-420 0)">' +
      '<image class="ink-l" href="' + p + 'sword-glow-light.png" x="0" y="-34" width="1024" height="68"/>' +
      '<image class="ink-d" href="' + p + 'sword-glow.png" x="0" y="-34" width="1024" height="68"/>' +
      '</g></g></g>' +

      /* ===== 墨烟（阶段3 形变遮挡：一道横向扫过的墨烟，0.1s 盖住形态切换） ===== */
      '<g class="ink-mist" opacity="0">' +
      '<animate attributeName="opacity" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.33, 1.39, 1.47, 1.57, DUR]) + '" values="0;0;0.62;0.48;0;0"/>' +
      '<g>' +
      '<animateTransform attributeName="transform" type="translate" dur="' + DUR + 's" repeatCount="indefinite"' +
      ' keyTimes="' + kt([0, 1.33, 1.53, DUR]) + '"' +
      ' values="-1260 ' + MIST_Y + ';-1260 ' + MIST_Y + ';1260 ' + MIST_Y + ';1260 ' + MIST_Y + '"/>' +
      '<image class="ink-l" href="' + p + 'brush-dry.png" x="0" y="-100" width="1500" height="200"/>' +
      '<image class="ink-d" href="' + p + 'brush-dry-dark.png" x="0" y="-100" width="1500" height="200"/>' +
      '</g></g>' +
      '</svg>'
    );
  }

  /* ------------------------------------------------------------------ */
  /* 样式                                                               */
  /* ------------------------------------------------------------------ */

  var STYLE_ID = 'ink-sword-style';
  var CSS = [
    '.ink-sword-wrap{position:relative;display:block;line-height:0}',
    '.ink-sword{display:block}',
    '.ink-sword .ink-d{display:none}',
    '.ink-sword.is-dark .ink-l{display:none}',
    '.ink-sword.is-dark .ink-d{display:inline}',
    '.ink-sword.is-static .ink-brush,',
    '.ink-sword.is-static .ink-blade,',
    '.ink-sword.is-static .ink-glow,',
    '.ink-sword.is-static .ink-mist,',
    '.ink-sword.is-static .ink-blob,',
    '.ink-sword.is-static .ink-splash,',
    '.ink-sword.is-static .ink-splash2{display:none}',
    '@keyframes inkBreath{0%,100%{opacity:.85}50%{opacity:1}}',
    '.ink-sword.is-static{animation:inkBreath 2.5s ease-in-out infinite}',
    '@media (prefers-reduced-motion: reduce){.ink-sword{animation:inkBreath 2.5s ease-in-out infinite}}',
    '.ink-fallback{display:flex;align-items:center;justify-content:center}',
    '.ink-fallback i{display:block;width:32px;height:32px;border:3px solid #2a2d3a;border-top-color:#7c5cfc;' +
      'border-radius:50%;animation:inkSpin .8s linear infinite}',
    '.ink-fallback.is-light i{border-color:rgba(26,43,69,.15);border-top-color:#1A2B45}',
    '@keyframes inkSpin{to{transform:rotate(360deg)}}'
  ].join('');

  function ensureStyle(doc) {
    if (doc.getElementById(STYLE_ID)) return;
    var el = doc.createElement('style');
    el.id = STYLE_ID;
    el.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(el);
  }

  /* ------------------------------------------------------------------ */
  /* 挂载                                                               */
  /* ------------------------------------------------------------------ */

  var FILES = [
    'arc-light.png', 'arc-dark.png', 'slash-light.png', 'slash-dark.png',
    'ink-blob.png', 'ink-blob-dark.png', 'ink-splash-a.png', 'ink-splash-a-dark.png',
    'ink-splash-b.png', 'ink-splash-b-dark.png',
    'brush-tip.png', 'brush-tip-dark.png', 'brush-dry.png', 'brush-dry-dark.png',
    'sword-glow.png', 'sword-glow-light.png'
  ];

  function preload(base) {
    var p = base.replace(/\/?$/, '/');
    return Promise.all(
      FILES.map(function (f) {
        return new Promise(function (resolve) {
          var img = new Image();
          img.onload = function () { resolve(true); };
          img.onerror = function () { resolve(false); };
          img.src = p + f;
        });
      })
    ).then(function (res) {
      return res.indexOf(false) < 0;
    });
  }

  function prefersReducedMotion(win) {
    try {
      return win.matchMedia && win.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) {
      return false;
    }
  }

  function mount(container, options) {
    options = options || {};
    var doc = container.ownerDocument || document;
    var win = doc.defaultView || window;
    var theme = options.theme === 'dark' ? 'dark' : 'light';
    var base = options.base || './assets/ink/';
    var size = options.size || 160;

    ensureStyle(doc);

    var wrap = doc.createElement('div');
    wrap.className = 'ink-sword-wrap';
    wrap.style.width = size + 'px';
    wrap.style.height = size + 'px';
    wrap.innerHTML = buildSvg(base);
    var svg = wrap.firstChild;
    if (theme === 'dark') svg.classList.add('is-dark');

    var onVis = null;
    var stopPerf = null;

    var api = {
      el: wrap,
      svg: svg,
      destroy: function () {
        if (onVis) doc.removeEventListener('visibilitychange', onVis);
        if (stopPerf) stopPerf();
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
      }
    };

    function freeze() {
      svg.classList.add('is-static');
      if (typeof svg.pauseAnimations === 'function') {
        svg.pauseAnimations();
        try { svg.setCurrentTime(FREEZE_T); } catch (e) { /* 忽略 */ }
      }
    }

    function start() {
      // 系统开启"减少动效"：不播放运动，直接展示定格签名帧 + 缓慢呼吸（§2.7）
      if (prefersReducedMotion(win)) { freeze(); return; }

      // 窗口不可见时暂停，恢复时继续，不重置进度（§2.6）
      onVis = function () {
        if (typeof svg.pauseAnimations !== 'function') return;
        if (doc.hidden) svg.pauseAnimations();
        else svg.unpauseAnimations();
      };
      doc.addEventListener('visibilitychange', onVis);
      if (doc.hidden) svg.pauseAnimations();

      // 低性能设备降级：连续丢帧则切定格帧（§2.7）
      if (typeof win.requestAnimationFrame === 'function') {
        var samples = [];
        var last = 0;
        var stopped = false;
        var tick = function (ts) {
          if (stopped) return;
          if (last) {
            samples.push(ts - last);
            if (samples.length >= PERF_FRAMES) {
              var avg = samples.reduce(function (a, b) { return a + b; }, 0) / samples.length;
              samples = [];
              if (avg > PERF_THRESHOLD_MS && !doc.hidden) { freeze(); stopped = true; return; }
            }
          }
          last = ts;
          win.requestAnimationFrame(tick);
        };
        win.requestAnimationFrame(tick);
        stopPerf = function () { stopped = true; };
      }
    }

    preload(base).then(function (ok) {
      if (!ok) {
        // 素材加载失败 → 回退到原有圆环，保证功能可用（§2.7）
        wrap.innerHTML = '';
        var fb = doc.createElement('div');
        fb.className = 'ink-fallback' + (theme === 'light' ? ' is-light' : '');
        fb.style.width = size + 'px';
        fb.style.height = size + 'px';
        fb.innerHTML = '<i></i>';
        wrap.appendChild(fb);
        api.svg = null;
        return;
      }
      container.appendChild(wrap);
      start();
    });

    return api;
  }

  global.MoShenInkSword = {
    mount: mount,
    FREEZE_T: FREEZE_T,
    DURATION: DUR,
    ARC_PATH: ARC_PATH
  };
})(typeof window !== 'undefined' ? window : this);
