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
// x-dialog —— 把一个 Alpine 布尔接到原生 <dialog> 上（2026-08-09）
//
// 用法：`<dialog x-dialog="open">`，`open` 是外层 x-data 里的布尔。
//
// 🔴 **全站所有覆盖层都必须经过这里，而这是一条结构性的要求，不是风格偏好。**
//
//    覆盖层原来是 `position: fixed; inset: 0` 的 div。而 `fixed` **不保证**相对
//    视口：祖先只要有 `transform` / `filter` / `contain` / `backdrop-filter`
//    中的任何一个，它就成了包含块，`inset: 0` 铺满的是那个祖先。
//    深色模式下 `.card` 和 `.wall` 都有 `backdrop-filter`（玻璃质感），
//    于是**两个覆盖层都中招**：改密码弹窗被关进卡片里（看得见）、
//    Memories 悬浮窗被关进 `.wall` 里（看不见，因为 `.wall` 恰好是满屏）。
//
//    ⚠️ 加 z-index 没有用。这跟层叠顺序无关，是包含块被换掉了。
//
//    `showModal()` 把元素放进 **top layer**，它的包含块永远是视口。
//    这是浏览器提供的唯一一个「祖先绝对够不着」的位置。
//
// ⚠️ **只用 `showModal()`，绝不用 `show()`，也绝不写 `open` 属性。**
//    后两者都只是把元素显示出来、**留在原地不进 top layer** —— 也就是把上面
//    那个 bug 原样请回来，而且现场看起来是对的（元素确实显示了）。
//    守卫测试盯着这一条，因为它是唯一一处「写错了也不报错」的地方。
//
// ⚠️ 双向同步，两个方向都必须有：
//      状态 → dialog   打开/关闭；
//      dialog → 状态   Esc 和 `close()` 是浏览器自己触发的，不写回去的话
//                      Alpine 那个布尔会停在 true，于是**第二次点按钮打不开**
//                      （状态没变，effect 不跑）。
//
// ⚠️ `close` 事件里先把 `syncing` 立起来，避免写回状态又触发 effect 再调一次
//    `close()` —— 那会在关闭时多跑一圈，虽然无害，但下一个人读到会以为是 bug。
Alpine.directive(
  "dialog",
  (el, { expression }, { effect, evaluateLater, cleanup }) => {
    const isOpen = evaluateLater(expression);
    const setClosed = evaluateLater(`${expression} = false`);
    let syncing = false;

    effect(() => {
      isOpen((open) => {
        if (syncing) return;
        if (open && !el.open) {
          el.showModal();
        } else if (!open && el.open) {
          syncing = true;
          el.close();
          syncing = false;
        }
      });
    });

    // Esc、以及任何调用了 close() 的地方。
    const onClose = () => {
      syncing = true;
      setClosed();
      syncing = false;
    };

    // 点遮罩关闭。⚠️ 判 `event.target === el`，不是 `.self` 那类修饰符：
    //    `::backdrop` 是伪元素、收不到事件，点在遮罩上时事件的 target 是
    //    dialog 元素**本身**。判等于是唯一能区分「点在遮罩上」和「点在内容上」
    //    的写法 —— 少了这一判，点输入框就会把弹窗关掉（同一个坑改版前也记着）。
    const onClick = (event) => {
      if (event.target === el) el.close();
    };

    el.addEventListener("close", onClose);
    el.addEventListener("click", onClick);
    cleanup(() => {
      el.removeEventListener("close", onClose);
      el.removeEventListener("click", onClick);
    });
  },
);

// ---------------------------------------------------------------------------
// 密码框的「看一眼」（2026-08-06）
//
// 挂在 core/components/field.html 上，按 widget 的 input_type 自动生效 ——
// 注册、登录、改密码三页共用这一份。
//
// ⚠️ 从 $root 里找那个 input，而不是靠 x-ref。x-ref 要写在元素自己身上，
//    而那个 input 是 Django 的 widget 渲染出来的 —— 想加属性就得去改 forms.py
//    的 widget attrs，而那是落点规矩明确禁止的（那里只放 type="date" 这类语义属性）。
//    组件只在自己的根节点里找，所以一页上有几个密码框互不干扰。
//
// ⚠️ 改的是 `type`，不是往里塞明文。所以密码管理器、自动填充、表单提交全都不受影响。
Alpine.data("passwordReveal", () => ({
  shown: false,

  toggle() {
    const input = this.$root.querySelector("input");
    if (!input) return;
    this.shown = !this.shown;
    input.type = this.shown ? "text" : "password";
  },
}));

// ---------------------------------------------------------------------------
// Memories 的悬浮窗（2026-08-06）
//
// 纯 UI 状态：开没开、开在第几张。符合 D24 对 Alpine 的口径 —— 这里没有任何
// 权限判断，也没有一行算术，两行照片的版面全部由 gallery/services.py 算好。
//
// ⚠️ 照片清单从页面里那个 `<script type="application/json">` 读，**不从 DOM 上
//    的 data-* 抓**。原因是墙上每张照片都有一份克隆（接缝用的），照 DOM 抓会
//    抓到两份，于是左右翻的时候每张都要按两次。JSON 那份是服务端给的那条
//    唯一序列，上行接下行，顺序就是箭头走的顺序。
//
// ⚠️ 大图的 URL 只出现在那段 JSON 里，`<img>` 上是缩略图。生产环境两者都是
//    签名过的临时 URL（见 config/settings/prod.py），所以整页会带着 120 个
//    长 URL —— 换成点开时再去问服务端要，可以省下那几十 KB，代价是每次点开
//    多一次往返。选了前者：翻看照片时的停顿比首屏几十 KB 更容易被感觉到。
Alpine.data("wall", () => ({
  photos: [],
  open: false,
  index: 0,

  // 鼠标停在带子上时，把速度乘以这个数。
  //
  // ⚠️ **1/2.5，不是 1/20**（2026-08-08 改）。原来是 0.05，实测反馈是
  //    「慢到我以为带子都停了」—— 而「停下来」是悬浮窗在做的另一件事
  //    （`.is-paused`，走 animation-play-state）。两种状态在屏幕上撞成了同一种，
  //    于是打开悬浮窗那一下就完全没有反馈了。
  //
  //    这里要的是「它还在走，只是给你时间看清楚」，所以下限不是「多慢都行」：
  //    **慢到看不出在动，就等于把停止那个状态偷走了。**
  //
  // ⚠️ **减速必须走 Web Animations 的 playbackRate，不能改 animation-duration。**
  //    改时长的话浏览器会按新时长重算「当前播到哪儿了」——
  //    同一个进度百分比对应的位置变了，带子会当场跳一大段。
  //    `updatePlaybackRate()` 是专门为这件事设计的：它在下一帧平滑接管，
  //    位置不动，只有速度变。
  //
  // ⚠️ 这个数**只在这里**。CSS 里一度还有一个 `--wall-linger`，没有任何东西读它 ——
  //    同一个数字的第二个家，迟早和真的那个对不上。
  lingerRate: 0.4,
  lingering: false,

  init() {
    const source = document.getElementById("wall-sequence");
    try {
      this.photos = JSON.parse(source?.textContent || "[]");
    } catch (e) {
      // 一段坏掉的 JSON 不该让整面墙不能滚动，只是点不开而已。
      this.photos = [];
    }
  },

  // 鼠标进出**一条**带子。
  //
  // ⚠️ 挂在带子上而不是逐张照片上：逐张挂的话，在相邻两张之间移动会连着触发
  //    leave + enter，速度一抖一抖 —— 而两张之间只隔十几像素，谁也瞄不准。
  //    缩小是逐张的（CSS :hover），减速是整条的。
  //
  // ⚠️ **拿传进来的那个元素找轨道，不用 `$refs`**（2026-08-08，三条带子那天）。
  //    Alpine 的 x-ref 在同一个作用域里同名出现多次时只留最后一个，
  //    所以照旧写 `this.$refs.track` 的话，鼠标停在第一条上会去减**第三条**的速 ——
  //    而那个 bug 的表现是「减速有时候有效有时候没效」，最难查的那一种。
  //    三条带子各自减各自的速。
  linger(strip, on) {
    this.lingering = on;
    const track = strip?.querySelector(".wall-track");
    if (!track) return;
    // ⚠️ getAnimations() 在「减少动效」下返回空数组（那时候根本没有动画），
    //    所以这里不需要额外判断 —— 循环体一次都不执行。
    for (const animation of track.getAnimations()) {
      animation.updatePlaybackRate(on ? this.lingerRate : 1);
    }
  },

  // ⚠️ 这两个方法**只管状态**了（2026-08-09）。焦点的三件事全部交回浏览器：
  //    打开时送进窗口（模板上的 `autofocus`）、Tab 关在里面、关掉后复位到
  //    原来那个元素 —— 都是模态 `<dialog>` 自带的。
  //    ⚠️ 其中「关掉后复位」以前**根本没做**：键盘用户关掉窗口后焦点回到
  //       <body>，再按 Tab 是从整页开头重新走一遍。
  show(index) {
    if (!this.photos.length) return;
    this.index = index;
    this.open = true;
  },

  close() {
    this.open = false;
  },

  step(delta) {
    const total = this.photos.length;
    if (!total) return;
    // 环形：最后一张往右翻回到第一张。⚠️ `+ total` 不能省 —— JS 的 % 对负数
    // 返回负数，少了它在第一张往左翻会得到 -1，然后是一张空白。
    this.index = (this.index + delta + total) % total;
  },

  get current() {
    return this.photos[this.index] || {};
  },
}));

// ---------------------------------------------------------------------------
// Google 预填的回调（2026-08-06）
//
// Google Identity Services 按 `data-callback` 的名字在 window 上找这个函数，
// 所以它必须挂在 window 上 —— 这是那个库的接口，不是我们的选择。
//
// ⚠️ 它做的事只有一件：把 credential 填进**我们自己那张带 csrf_token 的表单**
//    然后提交。不发 fetch、不解 token、不做任何判断 ——
//    token 归服务端验（accounts/google.py），因为浏览器里解出来的东西
//    任何人都能伪造，而这里唯一安全的做法是把它原样交给服务端。
//
// ⚠️ 表单不存在时什么都不做。注册页没配 client id 时这一整段标记都不输出，
//    而这个函数仍然会被打进 bundle —— 报一个 undefined 会进用户的 console。
window.onGoogleCredential = function (response) {
  const form = document.getElementById("google-prefill-form");
  if (!form || !response?.credential) return;
  form.querySelector('input[name="credential"]').value = response.credential;
  form.submit();
};

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

// ---------------------------------------------------------------------------
// 飘着的羽毛（2026-08-06）
//
// 公开首页和 /events/ 两页 —— 模板里有 .feather-sky 这一层它才启动，
// 别的页面上这段代码找不到容器就直接返回。**页面清单在模板那边，不在这里**：
// 这段代码从头到尾不认得任何一个 URL，加一页就是加一个 div。
// 素材是七片白羽毛（core/static/core/img/feather-*.webp）。
//
// 三条行为：飘（缓慢下落 + 左右摆）、被风吹（三档，见下）、落下消失。
// **没有「停在某处」** —— 那一条 2026-08-06 明确砍掉了，理由是落点自己在动
// （卡片挂着 scroll-breathe 每帧被写 transform、列表会被 HTMX 整个换掉），
// 一个「停住」的羽毛得每帧重新读目标位置才不穿帮，而那是这个功能里唯一会出怪相
// 的部分。
//
// ⚠️ 位置是**积分出来的**（vx / vy 两个速度），不是每帧加一个固定的下落量。
//    第一版是后者，于是风只能推着羽毛横着走 —— 「把它吹高」这件事在那个模型里
//    根本没有地方可落，而这一点从代码上看不出来，只有盯着页面看很久才发现
//    「羽毛好像只会从上面下来」。
//
// ⚠️ **这是全站唯一一个「不忙也在跑」的循环**，和上面滚动惯性那条「静止时自己
//    停下」的规矩正面冲突。冲突是主动接受的（羽毛按定义就是一直在动的），
//    代价用三件事压到最小，三件都不能删：
//
//      · 同时最多 2 片，多数时候 1 片，两片之间还留几秒空档；
//      · 一片都没有的时候用 setTimeout 等下一次出场，**不空转 rAF**；
//      · 标签页切走（document.hidden）立刻停，切回来再续。
//
//    ⚠️ 少了第三条，一个开着二十个标签页的人会有二十份 rAF 在后台跑 ——
//       而这件事在开发机上永远看不出来。
//
// ⚠️ 写的**只有 transform 和 opacity**，而且羽毛是自己那一层里的元素，
//    不碰任何卡片。这一条是明写的：卡片的 transform 归滚动惯性、translate 归
//    入场动画，两个通道都已经有主了（app.css 里判过刑的那一段）。
(function () {
  const REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)");

  //: 长边的像素数。50 ≈ 鼠标指针的 2.5 倍（2026-08-06 定），
  //: 上下浮动一点，因为一模一样大的羽毛看起来是同一片在循环。
  const SIZE_MIN = 38;
  const SIZE_MAX = 58;

  //: 下落速度（px/秒）。羽毛不是雨点 —— 快一点就变成落叶，再快就是下雪。
  const FALL_MIN = 16;
  const FALL_MAX = 34;

  //: 出场间隔（毫秒）。上一片走了之后隔多久来下一片。
  const GAP_MIN = 3500;
  const GAP_MAX = 13000;

  //: 同时两片的概率。「1 片占多数，2 片占少数」——「少数」在这里是 ~1/5。
  const SECOND_CHANCE = 0.22;

  const MAX_ALIVE = 2;

  //: 从哪条边进来（2026-08-06 加）。原来只有上边，实际看下来「只从天上掉」
  //: 很快就读得出规律 —— 风是有方向的，羽毛该从侧面被吹进来。
  //: ⚠️ 不从**下边**进。从底下升上来的羽毛需要一个持续的上升气流才说得通，
  //:    而这一页上没有任何东西暗示那件事，看起来只会像它掉反了。
  const FROM_TOP = 0.70;
  const FROM_LEFT = 0.85;   // 0.70–0.85 左边，其余右边

  //: 速度回到基线的快慢（每秒的指数逼近系数）。
  //: ⚠️ 这个数**单独决定强风看起来有多强**：大了，一记上吹几帧就被抹平，
  //:    羽毛只是抖一下；小了，羽毛被吹上去之后半天不下来，像断了线。
  //:    0.8 ≈ 1.25 秒的时间常数，一记强风的余韵大约两三秒。
  const SETTLE = 0.8;

  //: 每次阵风检查时，抽到「大动作」的概率。其余都是常态的微风。
  //:
  //: ⭐ **验收线：平缓的时间不得低于 35%**（2026-08-06 拍板，把原来的「九成」
  //:    换掉了）。也就是说这两个数还有很大的上调余地 —— 现行这一档量出来是 80%，
  //:    离线还有 45 个百分点。
  //:
  //: ⚠️ 但**必须配一次模拟才能改**，否则等于凭感觉调一个看不见的量。
  //:    「几成时间平缓」是对**结果**的要求，而这里调的是原因 —— 中间隔着检查
  //:    间隔、余韵长短、每片的存活时长三层，光看这两个数说不出结果是多少。
  //:    量出来的四次：
  //:
  //:      0.06 / 0.10 → 94.7% 平缓   （第一版，比当时要的还静，而「静了一点」
  //:                                  和「正常」长得一样，看不出来）
  //:      0.10 / 0.16 → 92.5% 平缓   （对着当时的「九成平缓」调的）
  //:      0.10 / 0.40 → 86.9% 平缓   （拍板要更常来的强风）
  //:      同上 + 加速度模型 → 80% 平缓（冲量改成一段加速度，弧线本来就比折角
  //:                                  费时间。当天验收线随后放宽到 35%）
  const LOOP_CHANCE = 0.10;
  const STRONG_CHANCE = 0.40;

  //: 保险丝：一片羽毛最多活这么久（秒），到点了淡出。
  //: ⚠️ 正常情况下永远碰不到（最慢的一片穿过 900px 也就 56 秒）。它挡的是
  //:    「被一记上吹送进某个再也回不来的状态」那一类 bug —— 而那类 bug 的表现是
  //:    页面上挂着一片永远不走的羽毛，外加一个永远不停的 rAF 循环。
  const MAX_LIFE = 140;

  function rand(lo, hi) {
    return lo + Math.random() * (hi - lo);
  }

  function start() {
    const sky = document.querySelector(".feather-sky");
    if (!sky || REDUCED.matches) return;

    // ⚠️ **七个完整的 URL 从模板来，这里一个都不拼。** 生产上静态文件走
    //    CompressedManifestStaticFilesStorage，文件名里带内容哈希
    //    （feather-1.webp → feather-1.<hash>.webp）—— 在 JS 里按序号拼路径的话，
    //    开发环境一切正常，**上线之后七张图全部 404**，而且页面不会报任何错，
    //    只是羽毛再也不出现了。
    const urls = (sky.dataset.feathers || "").split(",").filter(Boolean);
    if (!urls.length) return;

    // --- 点一片羽毛，进 Memories（2026-08-06）------------------------------
    //
    // ⚠️ 委托挂在 sky 上，而 sky 是 `pointer-events: none` —— 这不矛盾：
    //    不接事件的是**它自己**，从子元素（`.feather`，那边是 `auto`）冒上来的
    //    事件照收不误。挂到每片羽毛上也能work，但那要在 spawn 里逐片加、
    //    还要记得在回收时不泄漏，而这一层每分钟造几十片。
    //
    // ⚠️ URL 从模板的 data-* 来，不在这里写死。这个文件被 /events/ 和首页共用，
    //    而反解 URL 是 Django 的事 —— 何况 esbuild 的产物里写死一条路径，
    //    改路由的那天不会有任何东西报错。
    //
    // ⚠️ 没有 URL 就什么都不做，羽毛退回纯装饰。这一层在别的页面（或者
    //    这个属性哪天被漏掉时）不该变成一个点了没反应的东西。
    const memoriesUrl = sky.dataset.memoriesUrl;
    if (memoriesUrl) {
      sky.addEventListener("click", (event) => {
        if (event.target.classList?.contains("feather")) {
          window.location.href = memoriesUrl;
        }
      });
    }

    // ⚠️ 全部**提前解码**。不预载的话，一片羽毛出场的头几帧是空的 ——
    //    它会在半空中突然出现，而那正好是最显眼的时刻。
    for (const url of urls) {
      const img = new Image();
      img.src = url;
    }

    const alive = [];
    let running = false;
    let timer = null;
    let last = 0;

    function spawn() {
      if (alive.length >= MAX_ALIVE) return;

      const el = document.createElement("img");
      el.src = urls[Math.floor(Math.random() * urls.length)];
      el.alt = "";
      el.decoding = "async";
      el.className = "feather";
      // 长边定死、短边自己跟着走 —— 素材宽高比各不相同，约束两边会把某几片压扁。
      const size = rand(SIZE_MIN, SIZE_MAX);
      el.style.maxWidth = `${size}px`;
      el.style.maxHeight = `${size}px`;

      const w = window.innerWidth;
      const h = window.innerHeight;

      // --- 从哪条边进来，以及进来之后往哪走 -------------------------------
      //
      // ⚠️ 入场边和基线横速是**一起定的**，不能分开随机。从左边进来的羽毛
      //    要是没有一个持续向右的横速，它会贴着左边缘直直往下掉 ——
      //    看起来不是「被风吹进来」，是「在边上漏出来了」。
      const edge = Math.random();
      let x, y, driftX, fallBase;
      if (edge < FROM_TOP) {
        x = rand(0.05, 0.95) * w;
        y = -size - rand(10, 120);
        driftX = rand(-18, 18);
        fallBase = rand(FALL_MIN, FALL_MAX);
      } else {
        const fromLeft = edge < FROM_LEFT;
        x = fromLeft ? -size - rand(5, 40) : w + size + rand(5, 40);
        // 上半屏偏多：从侧面进来的羽毛还得有地方可落。
        y = rand(0.04, 0.62) * h;
        driftX = (fromLeft ? 1 : -1) * rand(30, 85);
        // 横着飘的时候落得慢一些，否则它在过屏之前就沉到底了。
        fallBase = rand(FALL_MIN * 0.6, FALL_MAX * 0.75);
      }

      const f = {
        el,
        x,
        y,
        size,
        // 速度是**状态**，不是常量（2026-08-06 改）。原来 y 每帧加一个固定的
        // fall，所以风只能推着它横着走 —— 一记「把它吹高」根本没有地方落。
        // 现在风改的是 vx / vy，两者各自往基线回落。
        vx: driftX,
        vy: fallBase,
        driftX,
        fallBase,
        // ⚠️ **两条正弦叠加**，周期不同且互质得不整齐 —— 一条正弦的左右摆
        //    是钟摆，看得出周期；两条叠起来人眼就找不到规律了。
        //    这是常态那段时间里「自然」的主要来源。
        swayA: rand(14, 44),
        swayB: rand(6, 22),
        periodA: rand(3.2, 7.5),
        periodB: rand(1.3, 2.9),
        phaseA: rand(0, Math.PI * 2),
        phaseB: rand(0, Math.PI * 2),
        // 常态下的慢转，正负都有。
        spin: rand(-14, 14),
        angle: rand(0, 360),
        // 阵风带来的额外转速，会自己衰减掉。
        // 当前这一阵风带来的额外转速（deg/s）。由包络算出来的**派生量**，
        // 不再自己累加和衰减 —— 见 frame() 里那段。
        gust: 0,
        // 正在刮的那一阵风：{ t0, dur, ax, ay, spin }。null = 现在没风。
        wind: null,
        gustAt: rand(1.5, 6),
        // 「打一个大圈」那种。null = 现在没在转圈。
        loop: null,
        // 有没有真的进过画面。
        // ⚠️ 没有它，从侧面进场的羽毛会**在第一帧就被判出界**：它出生在
        //    x ≈ -118，而摆动最多能再往外拉 66px，加起来就越过了左边的退场线 ——
        //    于是它还没进来就没了。表现不是报错，是「从侧面来的羽毛特别少」，
        //    而那看起来只像是随机数不走运。
        entered: false,
        t: 0,
        // 命中的话这一片不落到底，半路淡出 ——「时不时落下消失」。
        vanishAt: Math.random() < 0.3 ? rand(6, 14) : null,
        opacity: 0,
      };

      sky.appendChild(el);
      alive.push(f);

      // 另一片：只是**有时候**，而且不跟这一片同时出场。
      if (alive.length === 1 && Math.random() < SECOND_CHANCE) {
        setTimeout(() => { if (alive.length === 1) spawn(); }, rand(1200, 5200));
      }

      wake();
    }

    function schedule(delay) {
      clearTimeout(timer);
      timer = setTimeout(spawn, delay === undefined ? rand(GAP_MIN, GAP_MAX) : delay);
    }

    function retire(f) {
      f.el.remove();
      const at = alive.indexOf(f);
      if (at >= 0) alive.splice(at, 1);
      if (!alive.length) schedule();
    }

    function frame(now) {
      // ⚠️ 用真实的时间差推进，不是「每帧一格」。120Hz 的屏幕上羽毛会快一倍，
      //    而在开发机上永远看不出来。上限 50ms 是为了标签页切回来的那一帧 ——
      //    不封顶的话它会瞬间跳过好几秒。
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      const w = window.innerWidth;
      const h = window.innerHeight;

      for (let i = alive.length - 1; i >= 0; i--) {
        const f = alive[i];
        f.t += dt;

        // --- 风：三档，绝大多数时候是最轻的那一档 -----------------------
        //
        // ⚠️ 三档共用**同一个**到点检查，而不是各自一个定时器。三个定时器意味着
        //    强风和转圈可以同时开始，而那一下看起来不是「风大」，是羽毛坏了。
        //
        // 🔴 **风给的是加速度，不是一记瞬时的速度冲量。** 这一条是 2026-08-06
        //    改的，起因是肉眼在页面上看到了一个尖角。
        //
        //    `f.vx += mag` 这种写法让速度在**一帧之内**从 20 跳到 400 ——
        //    路径在那一点一阶不连续，而一条一阶不连续的曲线，眼睛读出来就是
        //    一个**角**。羽毛没有角，风也不是瞬间起的。
        //
        //    现在一阵风是一个持续 0.9–1.8 秒的加速度，配 sin² 包络
        //    （0 → 满 → 0，**两端斜率都是 0**）。加速度有界 ⇒ 速度连续 ⇒
        //    路径处处可导 ⇒ 只有弧，没有角。
        //
        //    ⚠️ 包络必须是 sin²，不能是 sin。sin 在两端值是 0 但**导数不是** ——
        //       加速度会瞬间从 0 跳到峰值，虽然比冲量温和得多，仍然是一次阶跃。
        if (f.t >= f.gustAt && !f.wind) {
          const pick = Math.random();
          if (pick < LOOP_CHANCE) {
            // 打一个大圈。半径先涨后收（sin² 包络，见下面画偏移那一段），
            // 所以它是从常态里**转出去再转回来**，而不是位置突然跳一下。
            const turns = rand(1.0, 1.6);
            const dur = rand(2.2, 4.2);
            const dir = Math.random() < 0.5 ? -1 : 1;
            f.loop = {
              t0: f.t,
              dur,
              // 半径比第一版大一档 —— 包络换成 sin² 之后它涨得慢，同一个数字
              // 画出来的圈明显更小。
              radius: rand(42, 98),
              omega: dir * turns * Math.PI * 2 / dur,
              phase: rand(0, Math.PI * 2),
            };
            // 转圈的时候羽毛自己也翻，否则它是平移着画了个圆，像被线牵着。
            f.wind = { t0: f.t, dur, ax: 0, ay: 0, spin: dir * rand(220, 420) };
            f.gustAt = f.t + dur + rand(2, 6);
          } else if (pick < LOOP_CHANCE + STRONG_CHANCE) {
            // 四面来的强风。方向取整个圆周 —— 包括从下往上，那就是「吹高」。
            //
            // ⚠️ **往上偏**（六成），不是均匀取。均匀取的话一半的强风是往下吹的，
            //    而往下那一半几乎看不出来：它和重力同向，羽毛只是快一点落下去。
            //    真正读得出「来了一阵风」的是被托上去的那一下。
            const upward = Math.random() < 0.6;
            const theta = upward ? rand(Math.PI, Math.PI * 2) : rand(0, Math.PI);
            const dv = rand(300, 640);
            const dur = rand(0.9, 1.8);
            // sin² 包络在一个周期上的积分是 dur/2，所以要凑出 dv 的速度变化，
            // 峰值加速度取 2·dv/dur。（SETTLE 会在这期间往回拉一点，
            // 所以实际到手的比 dv 小 —— 具体多少是量出来的，见模拟。）
            const peak = 2 * dv / dur;
            const down = Math.sin(theta);
            f.wind = {
              t0: f.t,
              dur,
              ax: Math.cos(theta) * peak,
              // ⚠️ 往下的**减半**。不减的话羽毛会被直接砸出屏幕下沿 ——
              //    一次「强风」的全部表现就成了它消失了。
              ay: down * peak * (down > 0 ? 0.45 : 1),
              spin: (Math.cos(theta) >= 0 ? 1 : -1) * rand(180, 380),
            };
            f.gustAt = f.t + dur + rand(2, 7);
          } else {
            // 常态的微风：推一下、转一点。
            // ⚠️ 推和转**同号**（同一个方向来的风），否则羽毛会转向一边、
            //    飘向另一边，那读起来不是风，是一个在拧的贴图。
            const dir = Math.random() < 0.5 ? -1 : 1;
            const dur = rand(0.7, 1.4);
            f.wind = {
              t0: f.t,
              dur,
              ax: dir * 2 * rand(22, 70) / dur,
              ay: 0,
              spin: dir * rand(70, 170),
            };
            f.gustAt = f.t + dur + rand(2, 7);
          }
        }

        // 把这一阵风积到速度上。包络两端斜率为 0，所以加速度本身也是连着的。
        let envelope = 0;
        if (f.wind) {
          const k = (f.t - f.wind.t0) / f.wind.dur;
          if (k >= 1) {
            f.wind = null;
          } else {
            const s = Math.sin(Math.PI * k);
            envelope = s * s;
            f.vx += f.wind.ax * envelope * dt;
            f.vy += f.wind.ay * envelope * dt;
          }
        }
        // 自转跟着同一条包络涨落，**不再靠指数衰减**：衰减那一版是先瞬间给满、
        // 再慢慢掉，也就是转速自己有一个阶跃。现在它 0 → 峰值 → 0 地走一遍。
        f.gust = f.wind ? f.wind.spin * envelope : 0;

        // 速度各自往基线回落。这一句就是「风会停」。
        f.vx += (f.driftX - f.vx) * SETTLE * dt;
        f.vy += (f.fallBase - f.vy) * SETTLE * dt;
        f.x += f.vx * dt;
        f.y += f.vy * dt;

        f.angle += (f.spin + f.gust) * dt;

        const sway =
          Math.sin(f.t / f.periodA * Math.PI * 2 + f.phaseA) * f.swayA +
          Math.sin(f.t / f.periodB * Math.PI * 2 + f.phaseB) * f.swayB;

        // 转圈是**画上去的偏移**，不是积分出来的：一个靠受力积分出来的圆
        // 半径由速度决定，而速度这时候正被风改着 —— 结果是个说不清的螺线。
        // 偏移能保证它真的转回原处。
        let loopX = 0;
        let loopY = 0;
        if (f.loop) {
          const k = (f.t - f.loop.t0) / f.loop.dur;
          if (k >= 1) {
            f.loop = null;
          } else {
            // 🔴 **sin² 包络，不是 sin。** 半径在两端都是 0，两个写法都满足；
            //    差别在**导数**：sin 的导数在 k=0 处是 radius·π/dur ≈ 111px/s，
            //    也就是转圈的那份位移速度**瞬间出现**，于是进圈和出圈各留一个
            //    折角 —— 正是 2026-08-06 在页面上肉眼看到的那个尖角的第二个来源。
            //    sin² 的导数两端都是 0，圈是从常态里长出来再收回去的。
            const s = Math.sin(Math.PI * k);
            const r = f.loop.radius * s * s;
            const a = f.loop.phase + f.loop.omega * (f.t - f.loop.t0);
            loopX = r * Math.cos(a);
            loopY = r * Math.sin(a);
          }
        }

        // 保险丝到点了就开始淡出，而不是硬删 —— 硬删是一片羽毛凭空消失。
        if (f.vanishAt === null && f.t > MAX_LIFE - 2.5) f.vanishAt = f.t;

        // 淡入 1.2 秒；结尾按两种走法之一淡出。
        let target = 0.85;
        if (f.t < 1.2) target = 0.85 * (f.t / 1.2);
        if (f.vanishAt !== null) {
          const left = f.vanishAt + 2.5 - f.t;
          if (left < 2.5) target = Math.min(target, 0.85 * Math.max(0, left / 2.5));
        } else {
          const below = f.y - (h - f.size * 2);
          if (below > 0) target = Math.min(target, 0.85 * Math.max(0, 1 - below / (f.size * 2)));
        }
        f.opacity = target;

        // 画出来的位置 = 积分出来的位置 + 摆动 + 转圈的偏移。
        const drawX = f.x + sway + loopX;
        const drawY = f.y + loopY;

        f.el.style.opacity = f.opacity.toFixed(3);
        // translate3d 而不是 translate：走合成层，不每帧回主线程重新布局。
        f.el.style.transform =
          `translate3d(${drawX.toFixed(1)}px, ${drawY.toFixed(1)}px, 0) ` +
          `rotate(${f.angle.toFixed(1)}deg)`;

        if (!f.entered && drawX > -f.size && drawX < w) f.entered = true;

        // ⚠️ 只有落到**下沿**和飘出**左右**才算走了。被吹到视口上方的**不算**：
        //    那正是「强风把它吹高了」该有的样子，而 vy 会自己落回基线把它送回来。
        //    第一版按「y 超出视口」退场，于是每一记上吹都以羽毛凭空消失收尾。
        const gone = f.vanishAt !== null
          ? f.t > f.vanishAt + 2.5
          : drawY > h + f.size;
        // 左右出界只对**已经进来过**的算数，理由见 entered 那一行。
        const offSide = f.entered
          && (drawX < -f.size * 2 || drawX > w + f.size * 2);
        if (gone || offSide) {
          retire(f);
        }
      }

      if (alive.length && !document.hidden) {
        requestAnimationFrame(frame);
      } else {
        running = false;
      }
    }

    function wake() {
      if (running || !alive.length || document.hidden) return;
      running = true;
      last = performance.now();
      requestAnimationFrame(frame);
    }

    // ⚠️ 标签页在后台时**两头都停**：循环停（frame 里那个条件），
    //    出场的定时器也停 —— 否则切回来会撞见一堆同时到期的 spawn。
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        clearTimeout(timer);
      } else if (alive.length) {
        wake();
      } else {
        schedule();
      }
    });

    // ⚠️ 第一片来得比之后的快。用常规间隔（3.5–13 秒）的话，加上它从视口上方
    //    飘下来那几秒，进页面之后最长要等**二十秒**才看得见任何东西 ——
    //    而一个「什么都没发生」的页面读起来是效果没生效，不是效果还没开始。
    //    之后的间隔照旧，那时候人已经知道这一页上有羽毛了。
    schedule(rand(600, 2200));
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

// ---------------------------------------------------------------------------
// 现场签到的二维码（D28）
//
// ⚠️ 这一段**不是 Alpine**，而这是有意的。Alpine 管的是展开/收起/选中这类纯 UI
//    状态；这里有网络请求、有定时器、有一个必须精确到秒的过期时刻 ——
//    塞进 x- 属性会立刻撞上守卫（里面不许有日期运算），而守卫是对的。
//
// ⚠️ 所有 URL 都从 data-* 读，一个都不拼。在 JS 里拼地址开发时完美、
//    部署后静默失效，而这里拼错的结果是一个**扫得动、但哪儿也去不了**的二维码。
import QRCode from "qrcode";

// 屏幕上这个码多久换一次。⚠️ 它和后端的有效期（90 秒）是一对：
// 差值决定了「扫到屏幕上的码之后手里还剩多久」，而那才是志愿者体验到的数字。
// 改这里必须回去看 events/tokens.py 的 MAX_AGE_SECONDS。
const REFRESH_MS = 20_000;

function checkinDisplay(root) {
  const canvas = root.querySelector("[data-checkin-canvas]");
  const error = root.querySelector("[data-checkin-error]");
  const countdown = root.querySelector("[data-checkin-countdown]");
  const bar = root.querySelector("[data-checkin-bar]");
  const buttons = [...root.querySelectorAll("[data-checkin-mode]")];

  let mode = root.dataset.mode;
  // 绝对时刻（毫秒），由服务端给。⚠️ **不是**「还剩几秒」的倒计数：
  // iPad 息屏或切后台之后浏览器会把定时器降频甚至冻结，自己数的那个数会停在
  // 半路，醒过来时页面以为码还新鲜 —— 而一个死掉的二维码和一个活的长得一模一样。
  // 给绝对时刻，睡多久醒来都算得出真相。
  let expiresAt = 0;
  // 这一张码从拿到手到失效有多久。⚠️ **不写死 90**，也不拿 REFRESH_MS 去缩放 ——
  // 那是第一版的做法，而它把两个不同的量当成了一个：屏幕上写着「New code in 82s」，
  // 可刷新是 20 秒一次；进度条按 20 秒缩放，于是 82/20 被 clamp 在 100%，
  // 那根条七十秒一动不动。写着一个数、量着另一个数、画出来第三个数。
  // 现在从服务端给的 expires_at 减去收到它的时刻，三者是同一个量。
  let lifetimeMs = 0;
  let timer = null;

  // ⚠️ 只改 aria-pressed，**不碰 class**。选中态长什么样写在模板的
  //    `aria-pressed:` 变体里 —— Tailwind 扫源码生成 CSS，只出现在 JS 字符串里的
  //    class 根本不会被生成出来。第一版正是那么写的，产物里一条
  //    `border-brand-600` 都没有，而页面照常渲染、测试照常绿。
  function paintMode() {
    for (const button of buttons) {
      button.setAttribute(
        "aria-pressed", button.dataset.checkinMode === mode ? "true" : "false");
    }
  }

  function showError(message) {
    // ⚠️ 盖住二维码，不是留着它。留着的话失败会静默转移到志愿者身上：
    //    他扫了、失败了、以为是自己手机的问题，而现场没人知道屏幕已经死了。
    canvas.hidden = true;
    error.hidden = false;
    error.textContent = message;
    expiresAt = 0;
    countdown.textContent = "Not working";
    bar.style.width = "0%";
  }

  async function refresh() {
    try {
      const response = await fetch(
        `${root.dataset.tokenUrl}?mode=${encodeURIComponent(mode)}`,
        { headers: { Accept: "application/json" }, credentials: "same-origin" },
      );
      const data = await response.json();
      if (!response.ok) {
        showError(data.error || "Check-in is not open for this event.");
        return;
      }
      await QRCode.toCanvas(canvas, data.url, { width: 320, margin: 1 });
      expiresAt = data.expires_at * 1000;
      lifetimeMs = Math.max(1, expiresAt - Date.now());
      canvas.hidden = false;
      error.hidden = true;
    } catch {
      // 网络断了。⚠️ 教堂大厅的 wifi 会比数据库先跪，所以这条路径是常态不是意外。
      showError("Reconnecting…");
    }
  }

  // ⚠️ 数的是**屏幕上这张码还能用多久**，不是「还有几秒刷新」。后者是页面自己的
  //    实现细节，前者才是志愿者扫下去会不会成功。而且正因为它读的是绝对时刻，
  //    息屏之后回来它会直接归零 —— 这就是「死掉的码和活的长得一模一样」这件事
  //    唯一能被看见的地方。归零就把码盖掉，绝不留着。
  function tick() {
    if (!expiresAt) return;
    const left = Math.max(0, expiresAt - Date.now());
    if (!left) {
      showError("Code expired — reconnecting…");
      return;
    }
    countdown.textContent = `Code expires in ${Math.ceil(left / 1000)}s`;
    bar.style.width = `${Math.min(100, (left / lifetimeMs) * 100)}%`;
  }

  function start() {
    clearInterval(timer);
    refresh();
    timer = setInterval(refresh, REFRESH_MS);
  }

  for (const button of buttons) {
    button.addEventListener("click", () => {
      mode = button.dataset.checkinMode;
      paintMode();
      start();
    });
  }

  // ⚠️ 回到前台立刻重取。少了这一条，前面那些绝对时刻的讲究全是白费 ——
  //    页面会正确地知道码已经死了，却要等到下一个 setInterval 才去换一个。
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) start();
  });
  window.addEventListener("focus", start);

  paintMode();
  start();
  setInterval(tick, 250);
}

for (const root of document.querySelectorAll("[data-checkin-display]")) {
  checkinDisplay(root);
}

// ---------------------------------------------------------------------------
// 日程：红线、倒计时、以及「已经过去」那半透明（2026-08-18）
//
// 这一段接替了占位框那段尺寸读数 —— 那段的全部用途是量右边空出来多大，
// 量完了，连同 `.schedule-placeholder` 一起删了。
//
// 🔴 **这里不做日期运算。** 翻页翻到哪一天是服务端算好的（三对箭头，
//    events/schedule.py 的 navigation），这里只做一件算术：把两个**绝对时刻**
//    相减。差值和时区无关 —— 这正是为什么模板给的是 epoch 毫秒，
//    而不是「今天几点」。
//
//    ⚠️ 用浏览器本地的午夜去算红线位置是错的，而且是那种在开发机上永远看不见的
//       错：基金会的时区是洛杉矶（D16），一个在纽约的志愿者看到的红线会差三小时，
//       页面上一切正常。
//
// ⚠️ 服务端已经把这三样都渲染好了（红线位置、倒计时、半透明），这里是**刷新**，
//    不是初始化。没有 JS 的人看到的是打开页面那一刻的正确状态；有 JS 的人看到
//    的是一直对的状态。反过来写（服务端不渲染、全靠 JS）会让没有 JS 的人得到
//    一张没有红线的日历，而红线是这一页最有用的东西。
const SCHEDULE_TICK_MS = 30000;

// 一小时多少像素。⚠️ 必须和 app.css 的 `--schedule-hour`、以及
//    events/schedule.py 的 PX_PER_HOUR 相等。三份，因为三层各自都要用到它，
//    而它们之间没有任何东西连着 —— 从 CSS 变量里读，就少一份手抄。
function scheduleHourPx(root) {
  const raw = getComputedStyle(root).getPropertyValue("--schedule-hour");
  return parseFloat(raw) || 48;
}

function paintSchedule(root) {
  const now = Date.now();
  const hourPx = scheduleHourPx(root);

  // 已经结束的卡片半透明。⚠️ 每一次都两个方向都设，不能只加不减 ——
  //    翻页换进来的是新的一批卡，而这个函数也跑在它们身上。
  for (const card of root.querySelectorAll("[data-schedule-card]")) {
    card.classList.toggle("is-past", Number(card.dataset.end) <= now);
  }

  for (const line of root.querySelectorAll("[data-schedule-now]")) {
    const dayStart = Number(line.dataset.dayStart);
    // ⚠️ 跨过午夜时这条线会走出它那一列的底部。它属于的那一天已经不是今天了，
    //    整块日程该重取 —— 但在那之前，把线藏起来，而不是让它挂在列外面。
    const minutes = (now - dayStart) / 60000;
    if (minutes < 0 || minutes >= 24 * 60) {
      line.hidden = true;
      continue;
    }
    line.hidden = false;
    line.style.top = `${Math.round((minutes / 60) * hourPx)}px`;

    // 红线正压着的那些卡里，最快结束的那一场还剩多久。
    // ⚠️ 和服务端 _soonest_ending 是同一条规则。两份实现，因为一份要在没有 JS
    //    时也成立 —— 分叉的表现是刷新一下数字跳一下，所以两边的取整方式
    //    （都向上取整到分钟）也必须一样。
    const column = line.closest(".schedule-col");
    let soonest = null;
    for (const card of column.querySelectorAll("[data-schedule-card]")) {
      const start = Number(card.dataset.start);
      const end = Number(card.dataset.end);
      if (start <= now && now < end && (soonest === null || end < soonest)) {
        soonest = end;
      }
    }

    let pill = line.querySelector("[data-schedule-left]");
    if (soonest === null) {
      if (pill) pill.remove();
      continue;
    }
    if (!pill) {
      pill = document.createElement("span");
      pill.className = "schedule-now-left";
      pill.dataset.scheduleLeft = "";
      line.appendChild(pill);
    }
    pill.textContent = remainingText(soonest - now);
  }
}

// "1h 21m left" / "42m left"。⚠️ 向上取整，和 schedule.remaining() 一致 ——
//    向下取整的话最后 59 秒写的是「0m left」，而那一分钟活动还在进行。
function remainingText(ms) {
  const minutes = Math.max(0, Math.ceil(ms / 60000));
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}m left`;
  return rest ? `${hours}h ${rest}m left` : `${hours}h left`;
}

// 打开时滚到哪儿（2026-08-18 定：7am 打底，现在更晚就跟着现在）
//
// 🔴 **不从 0:00 开始。** 一天里最不可能有活动的正是凌晨那几个小时，而日程第一眼
//    如果是一块空白，人得先自己往下拖才知道有没有东西 —— 一屏日历的全部意义
//    就是不必先做这一步。
//
// 规则只有两条，取更晚的那个：
//   ① 7am 打底 —— 于是 7am–6pm 那 11 小时落在一屏里（11 × 48 = 528px，
//      而 900px 高的窗口上可视区约 526px，正好）。
//   ② 「现在」不许掉到屏幕外 —— 傍晚打开时窗口跟着往下走。
//
// ⚠️ 凌晨的活动**不会**把窗口自己往上拽。这是明确选择的（2026-08-18）：
//    宁可让那种少见的情况多滚一下，也不要「每翻一页起点都不一样」。
//    往上滚一下就看得见，而不可预测的起点是每次都要重新找位置。
const SCHEDULE_OPENS_AT_HOUR = 7;

function scrollScheduleIntoView(root) {
  const scroller = root.querySelector("[data-schedule-scroll]");
  if (!scroller) return;
  const hourPx = scheduleHourPx(root);
  let top = SCHEDULE_OPENS_AT_HOUR * hourPx;

  // 「现在」在这个窗口里，而且它已经晚到快要看不见了 —— 跟着它走。
  // ⚠️ 减掉三分之一屏而不是把红线顶在最上面：刚过去的那一场是人最常回头看的。
  const line = root.querySelector("[data-schedule-now]:not([hidden])");
  if (line) {
    top = Math.max(top, parseFloat(line.style.top) - scroller.clientHeight / 3);
  }

  // ⚠️ 夹在合法范围里。日程末尾那几个小时是空的，`scrollTop` 设过头浏览器会
  //    自己截断 —— 但**截断之后读回来的值和写进去的不一样**，而下面没有人再读它。
  //    写下来是因为将来若要「记住滚动位置」，那件事会从这里开始出错。
  scroller.scrollTop = Math.max(
    0, Math.min(top, scroller.scrollHeight - scroller.clientHeight));
}

// 🔴 **一个计时器、一个监听，挂在文档上，不是一块日程一份。**
//    翻页和筛选都会把整块日程换成新的 DOM。每换一次就新挂一份的话，旧的那份
//    既不会被回收也没人停得掉它 —— 翻五十次页就是五十个 `setInterval` 对着
//    五十棵已经脱离文档的树跑，外加五十个永远摘不掉的 `visibilitychange`。
//    这在浏览器里看不出来（画面全对），只有页面开久了才慢慢变卡。
//
// ⚠️ 2026-08-18：改「打开时滚到哪儿」那次，把这一整段连同上面的函数一起删掉了 ——
//    红线和「已结束」的刷新就此停摆，而屏幕上打开的第一眼完全正常，
//    要等一分钟才看得出线没动。是守卫抓住的
//    （ScheduleGeometryGuardTests.test_the_clock_is_one_timer_on_the_document_not_one_per_block），
//    不是人看出来的。
function paintAllSchedules() {
  for (const root of document.querySelectorAll("[data-schedule]")) paintSchedule(root);
}

// ⚠️ 回到前台立刻重画。息屏半小时之后回来，`setInterval` 在后台被节流得很凶，
//    红线可能停在半小时前 —— 而那正是「一条画错位置的红线」。
setInterval(paintAllSchedules, SCHEDULE_TICK_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) paintAllSchedules();
});

// 每一块日程只需要做一次的事：先画一遍，然后滚到该看的地方。
// ⚠️ `data-schedule-live` 挡的是**重复滚动**：afterSettle 一次请求会触发两回
//    （主体一次，out-of-band 一次），少了它，人刚拖到的位置会被第二回拽回去。
function startSchedule(root) {
  if (root.dataset.scheduleLive) return;
  root.dataset.scheduleLive = "1";
  paintSchedule(root);
  scrollScheduleIntoView(root);
}

function bootSchedules(scope) {
  for (const root of scope.querySelectorAll("[data-schedule]")) startSchedule(root);
}

bootSchedules(document);

// ---------------------------------------------------------------------------
// 筛选卡钉住之后：把它的实测高度写成 `--filter-h`（2026-08-19）
//
// 🔴 **钉住的东西会挡住「滚到这里」。** 翻页用 `show:#event-results:top` 把结果区
//    顶到视口最上面，而钉住的筛选卡正好压在那儿 —— 翻到下一页，第一张卡完全
//    看不见。修法是给结果区一段 `scroll-margin-top`，而那段距离要知道卡有多高。
//
// ⚠️ 不能写死一个数：这张卡窄屏上控件换行、表单报错时多一行字，高度是会变的。
//    写死的话那两种情况下要么还挡着、要么下面空一截。
//
// ⚠️ 用 ResizeObserver 而不是监听 resize：高度变化有两个来源 —— 拖窗口，
//    和开关日程时那 560ms 的过渡里列宽变化带来的换行。后者根本不触发 resize，
//    于是读数会停在过渡前的值，也就是恰好在最需要它的时候是错的。
//    （同一条理由，上一批那个已删的占位框读数也是这么写的。）
//
// ⚠️ 写在 `.events-shell` 上而不是 `:root`：这个变量只有这一页用，
//    挂到根上就是给全站加一个只有一页认识的全局。
function watchFilterHeight() {
  const card = document.querySelector(".filter-card");
  const shell = document.querySelector(".events-shell");
  if (!card || !shell || typeof ResizeObserver === "undefined") return;
  new ResizeObserver(() => {
    shell.style.setProperty("--filter-h", `${Math.round(card.offsetHeight)}px`);
  }).observe(card);
}

watchFilterHeight();

// ---------------------------------------------------------------------------
// 点日程上的一张卡：左边翻到那一场、滚进视口、套一圈高亮（2026-08-18）
//
// 服务端在那一次请求里已经把左边换成了正确的一页、并且画上了 `is-picked`。
// 这里做的是它做不到的两件事：
//
//   ① **滚进视口** —— 服务端渲染不了滚动位置。
//   ② **让高亮活得比那一次响应长** —— 之后每一次筛选、每一次翻页都会把整块
//      列表换掉，而新换进来的那一份不知道刚才点的是谁。
//
// 🔴 这里**不记「哪一页」**，只记一个 pk。记页码的话，筛选一变，同一个页码
//    指向的是另一批人 —— 而高亮会安静地落在一个陌生的活动上。
let pickedEvent = null;

function paintPicked() {
  for (const row of document.querySelectorAll("[data-event]")) {
    // ⚠️ 两个方向都设。只加不减的话，翻一页回来会留下两圈高亮。
    // ⚠️ `[data-event]` 同时命中日程上的卡片和列表里的行 —— 前者没有
    //    `.event-row`，而 `.is-picked` 的样式挂在 `.event-row.is-picked` 上，
    //    所以给卡片加上这个 class 不会画出任何东西。留着是有意的：将来若要
    //    在日程那边也标一下「正开着的是这张」，钩子已经在了。
    row.classList.toggle("is-picked", String(pickedEvent) === row.dataset.event);
  }
}

function scrollPickedIntoView() {
  if (pickedEvent === null) return;
  const row = document.querySelector(
    `.event-row[data-event="${CSS.escape(String(pickedEvent))}"]`);
  // ⚠️ 找不到是**正常**的：那一场可能不在左边的列表里（日程画的是那几天的
  //    全部，列表还带着「今天起」那一刀），窄屏上那一整列更是 display:none。
  //    静静地什么都不做，不是报错。
  if (!row || !row.offsetParent) return;
  // ⚠️ `block: "center"` 而不是 `"start"` —— start 会把那一行顶到吸顶导航栏
  //    底下去，正好被盖住一截。
  row.scrollIntoView({ behavior: "smooth", block: "center" });
}

// 记下点的是谁。⚠️ 用事件委托挂在 body 上，不是给每张卡各挂一个 ——
//    卡片每次翻页都被整批换掉，逐张挂等于每翻一次页漏一批监听。
// ⚠️ `[data-event]`，不限于日程上的卡片（2026-08-19）：左边列表那一行也带着它，
//    而从左边点开的那一场同样要圈住。两处点击是同一件事的两个入口，
//    「右边正开着的是哪一场」只有一个答案。
document.body.addEventListener("click", (event) => {
  const card = event.target.closest("[data-event]");
  if (!card) return;
  pickedEvent = card.dataset.event;
});

// 每一次 HTMX 落地之后补一次。⚠️ `afterSettle` 而不是 `afterSwap`：
//    左边那一列是 out-of-band 换进来的，afterSwap 时还没落到文档里。
document.body.addEventListener("htmx:afterSettle", () => {
  paintPicked();
  scrollPickedIntoView();
});

// 翻页和筛选都会把整块日程换掉（一个是普通 swap，一个是 out-of-band），
// 换进来的是全新的 DOM。
// ⚠️ `htmx:afterSettle` 而不是 `afterSwap`：out-of-band 的那一块在 afterSwap
//    时还没落到文档里，于是筛选之后日程会变成一块没有红线、也不会自己滚的
//    静态图 —— 而它看起来完全正常。
//    ⚠️ 从 document 起扫，不从 `event.target` 起：out-of-band 换掉的那一块
//       **不是** target（target 是列表），从 target 起扫会正好漏掉日程。
document.body.addEventListener("htmx:afterSettle", () => bootSchedules(document));
