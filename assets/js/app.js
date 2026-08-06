// 前端的全部 JavaScript。产物是 static/js/app.js（esbuild 打包 + 压缩）。
//
// 三层分工在 D24，两条硬规矩在 phase-c.md 三、落点规矩：
//
//   HTMX  —— ⭐ 每一个写操作都必须有一条完整的服务端表单路径，HTMX 只是它的快路。
//            读操作（筛选、排序、展开详情）不受这条约束。
//   Alpine —— 只管展开/收起/选中/模态/主题切换这类纯 UI 状态。
//            x- 属性里不许出现权限判断、工时或金额的计算、日期运算。
//
// ⚠️ Alpine 的越界不会报错。把权限判断写进 x-show，页面照常渲染、测试照常绿，
//    只是那个按钮对不该看见它的人也画出来了。守卫测试为这一条存在（C2.6）。

import "htmx.org";
import Alpine from "alpinejs";

// ---------------------------------------------------------------------------
// 主题切换
//
// ⚠️ 这里**不负责首帧**。页面加载时该不该是深色，由 base.html <head> 里那段
//    内联脚本决定 —— 它必须在渲染之前跑完。这个文件是打包产物，带 defer，
//    等它跑起来页面已经画过一遍了，那一下闪白是「廉价感」最大的单一来源。
//
//    所以这两处**读的是同一个 key、同一套规则**，只是时机不同：
//    内联脚本管「进来时是什么」，这里管「点了按钮之后变成什么」。
//    改其中一处必须改另一处 —— 分叉的表现是刷新一下主题就跳回去。
const THEME_KEY = "theme";

function applyTheme(theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

Alpine.data("themeToggle", () => ({
  // 状态名照 design-system.md 的约定：open / selected / theme，不每页起新名字。
  theme: document.documentElement.classList.contains("dark") ? "dark" : "light",

  toggle() {
    this.theme = this.theme === "dark" ? "light" : "dark";
    // 写进 localStorage 才算「手动开关」。不写的话下次进来又回到跟随系统，
    // 而用户刚刚明确表达过他不想跟随系统。
    localStorage.setItem(THEME_KEY, this.theme);
    applyTheme(this.theme);
  },
}));

// ---------------------------------------------------------------------------
// HTMX 的两条全局约定

// 1. 每个片段请求带上当前主题以外什么都不带 —— CSRF 走 base.html 上那一次
//    hx-headers，不在这里重复配置。

// 2. 加载态：hx-indicator 指到的元素在请求进行中显示。
//    ⚠️ 没有加载态的按钮，用户会点第二次 —— 而第二次点的是一个写操作。
document.body?.addEventListener("htmx:responseError", (event) => {
  // HTMX 默认把非 2xx 的响应体丢掉，于是 403 / 500 在片段请求里表现为
  // 「点了没反应」。整页表单路径上这些都会跳到 403.html / 500.html，
  // 片段路径上不会 —— 所以这里至少把它说出来。
  const status = event.detail?.xhr?.status;
  if (status) {
    console.warn(`htmx: server refused with ${status}`, event.detail.pathInfo?.requestPath);
  }
});

window.Alpine = Alpine;
Alpine.start();

// ---------------------------------------------------------------------------
// 滚动惯性（2026-08-05）
//
// 每张卡片**滞后于滚动**一点点，而且每张滞后得不一样多 —— 于是快速拖动时卡片
// 之间的间距看起来被拉开又合拢，松手后弹回对齐。
//
// ⚠️ 这一版**取代了**原来纯 CSS 的滚动驱动动画，那一版已整个删掉。理由不是它
//    坏了：`animation-timeline: view()` 知道的是元素在视口里的**位置**，而
//    「拖拽的惯性」需要的是**速度**。那是那个技术的边界，不是参数没调够。
//    两套并存会同时写同一个元素的 transform，谁最后写谁赢 —— 必然出 bug。
//
// ⚠️ 动的始终只有 transform。真去改 margin 会让页面总高度不断变化，浏览器要
//    重排、滚动位置和手指打架 —— 那是抖，不是惯性。间距是**看起来**不均匀的：
//    相邻两张卡片位移不同，中间那道缝就宽窄不一。视觉上和真改间距没有区别。
//
// ⚠️ 循环在静止时**自己停下**。一个永远在跑的 rAF 循环会让手机一直保持高频唤醒，
//    而这一整个效果只在手指在动的那半秒里存在。
(function () {
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");
  //: 速度换成位移的比例，和位移的上限（px）。上限存在是因为一次
  //: 「滚到底」的滚轮事件速度可以是几千 —— 没有它卡片会被甩出屏幕。
  const GAIN = 0.28;
  const MAX_OFFSET = 18;

  function start() {
    const cards = Array.from(document.querySelectorAll(".scroll-breathe"));
    if (!cards.length || REDUCED.matches) return;

    // ⚠️ 每张卡片一个略微不同的跟随系数，这才是「间距不一致」的来源。
    //    全都一样的话它们会整体平移，看起来只是页面在晃。
    //    用序号推出来而不是随机：刷新之后同一张卡片的行为要一样，
    //    否则每次滚动的手感都不同，像坏了。
    const state = cards.map((_, i) => ({
      offset: 0,
      follow: 0.10 + 0.055 * (((i * 7) % 5) / 4),
    }));

    let lastY = window.scrollY;
    let velocity = 0;
    let running = false;

    function frame() {
      let awake = false;
      for (let i = 0; i < cards.length; i++) {
        const s = state[i];
        const target = Math.max(-MAX_OFFSET, Math.min(MAX_OFFSET, velocity * GAIN));
        s.offset += (target - s.offset) * s.follow;
        if (Math.abs(s.offset) > 0.05) awake = true;
        // translate3d 而不是 translateY：前者保证走合成层，
        // 不会每帧回到主线程重新布局。
        cards[i].style.transform = `translate3d(0, ${s.offset.toFixed(2)}px, 0)`;
      }
      // 速度自己衰减 —— 手指停了之后卡片还要「追」几帧才归位，
      // 那几帧就是惯性看起来的样子。
      velocity *= 0.82;
      if (Math.abs(velocity) < 0.5) velocity = 0;

      if (awake || velocity !== 0) {
        requestAnimationFrame(frame);
      } else {
        running = false;
        for (const card of cards) card.style.transform = "";
      }
    }

    window.addEventListener("scroll", () => {
      const y = window.scrollY;
      // ⚠️ 累加而不是覆盖：一次滚动里会收到很多次 scroll 事件，
      //    覆盖的话只有最后一小段位移算数，快速滚动反而没有惯性。
      velocity += y - lastY;
      lastY = y;
      if (!running) {
        running = true;
        requestAnimationFrame(frame);
      }
    }, { passive: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
