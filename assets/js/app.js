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
  // 打开之前焦点在哪。关掉之后要放回去 —— 不放回去的话，键盘用户关掉窗口后
  // 焦点回到 <body>，再按 Tab 是从整页开头重新走一遍。
  returnTo: null,

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

  show(index) {
    if (!this.photos.length) return;
    this.returnTo = document.activeElement;
    this.index = index;
    this.open = true;
    // $nextTick：这一刻窗口还没画出来，querySelector 找不到那个按钮。
    this.$nextTick(() => this.$refs.close?.focus());
  },

  close() {
    this.open = false;
    this.returnTo?.focus?.();
    this.returnTo = null;
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

  // Tab 关在窗口里。⚠️ 少了这一条，键盘用户按 Tab 会走到窗口**后面**那面墙上，
  // 屏幕上盖着一个他已经离开、却又关不掉的对话框。
  trap(event) {
    const focusable = this.$refs.dialog?.querySelectorAll("button");
    if (!focusable?.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
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
