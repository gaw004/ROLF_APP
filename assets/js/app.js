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

// ⚠️ 具名地 import 进来，不是 `import "htmx.org"`（2026-08-28 改）。
//    这个包**不往 window 上挂自己** —— 打包之后 `window.htmx` 是 undefined，
//    而属性和 hx- 属性照常工作，所以「htmx 在不在」这件事只有需要调它的
//    JS API 时才会暴露出来。下面 Clear 那一段要 `htmx.trigger`。
import htmx from "htmx.org";
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
// 受众勾选框之间的包含关系（2026-08-27）
//
// 三档勾选是**包含**关系，不是并列：「所有人」= 外部 + 全体在编，而「全体在编」
// 已经包含每一个 ministry。服务端两条规则拒绝这些冗余组合
// （events/models.py 的 refuse_redundant_audience），这里只是**提前**告诉人
// 哪几个已经被上面那个勾包住了 —— 否则发现这条规则的唯一方式是提交一次挨报错。
//
// ⚠️ 纯 UI 状态，没有任何判断落在这里（D24）：谁能看见活动仍然只由服务端决定。
//
// 🔴 **只禁用「没有值」的那些**（`disabled = covered && !checked`），
//    而这一行是这个组件唯一真正微妙的地方。禁用的控件不随表单提交 —— 所以无脑
//    禁用会让一场原本勾着「报税互助」的活动，在管理员补勾「全体在编」时把报税互助
//    **静默丢掉**：那两个值从此进不了 POST，服务端那条「同一个状态两种存法」的拒绝
//    根本不触发。没有 JS 的同一次提交会被拒绝并要求他自己选。
//    有 JS 和没 JS 存进不同的结果 —— 正是 D24 说增强永远不许做的事。
//    ⚠️ 而按值禁用之后，报错信息里那句「取消它，或者取消那几个 ministry」也真的
//       可点了：被盖住**且勾着**的那几个仍然点得动，正是人要去取消的那几个。
//
// ⚠️ 「所有人」那一对不受这条影响（勾了它，那两个的值由服务端从它推出来），
//    但判据仍然统一写成一条 —— 两种规则会在某一格上走散。
//
// ⚠️ 照 passwordReveal 那条规矩从 $root 里按 name 找控件，**不靠 x-ref、
//    也不往 forms.py 的 widget attrs 里塞 x- 属性** —— 那里只放语义属性。
//    于是任何一张带受众的表单只要在 <form> 上加一句 x-data 就有了这个行为。
//    ⚠️ 名字只在这一份声明里出现，events/tests.py 会把它读出来跟 Django 渲染的
//       对 —— 两边任一改名都变红。改这里就是改那份契约。
const AUDIENCE_BOXES = {
  everyone: "audience_is_everyone",
  outsiders: "visible_to_outsiders",
  allStaff: "visible_to_all_staff",
  ministries: "visible_to_ministries",
};

Alpine.data("audienceTicks", () => ({
  init() {
    // ⚠️ 监听挂在整个 <form> 上（change 不冒泡到别处），所以先问一句改的是不是
    //    受众里的控件 —— 否则改一次活动名、时间、状态、图片都要把整张表单扫五遍。
    this.$root.addEventListener("change", (event) => {
      if (Object.values(AUDIENCE_BOXES).includes(event.target.name)) this.sync();
    });
    this.sync();
  },

  boxes(name) {
    return Array.from(this.$root.querySelectorAll(`[name="${name}"]`));
  },

  sync() {
    const everyone = this.boxes(AUDIENCE_BOXES.everyone)[0];
    const allStaff = this.boxes(AUDIENCE_BOXES.allStaff);
    const wide = Boolean(everyone && everyone.checked);
    this.cover(this.boxes(AUDIENCE_BOXES.outsiders), wide);
    this.cover(allStaff, wide);
    this.cover(
      this.boxes(AUDIENCE_BOXES.ministries),
      wide || allStaff.some((box) => box.checked),
    );
  },

  // ⚠️ 灰的不只是那个小方块。`field.html` 和 Django 的多选 widget 各自渲染
  //    自己的 <label>，而一个满对比度的标签配一个变淡的框，读起来是页面坏了。
  //    所以给包着它的 <label> 或 .field 加一个 class，样式挂在 app.css 上
  //    —— 样式落点规矩：这里不写任何一条 CSS，连 cursor 都不写（那条在 base 层）。
  cover(boxes, covered) {
    boxes.forEach((box) => {
      box.disabled = covered && !box.checked;
      const holder = box.closest("label") || box.closest(".field");
      if (holder) holder.classList.toggle("audience-covered", covered);
    });
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

// 字节到齐之后，最多再等解码这么久，然后不管解没解完都换上去（2026-09-09）。
//
// 🔴 这是个**上限，不是等待时长**：正常情况下 `decode()` 早就返回了，这个计时器
//    什么都不做。它存在只为一件事 —— `decode()` 没有延迟上界，而它挂住的代价是
//    照片永远停在缩略图上，不报错也不空白，只是一直有点糊。
//
// ⚠️ 400 的来路：1600px 的 WebP 在现代硬件上解码 10–40ms，老手机上一两百毫秒，
//    所以 400 稳稳在正常值之上 —— 真实的解码不会撞到它，撞到它的一定是卡住了。
//    调小到接近正常解码耗时，就会开始在慢机器上抢在解码前换图，
//    把 `decode()` 想避免的那一帧卡顿放回来。
const DECODE_CEILING_MS = 400;

Alpine.data("wall", () => ({
  photos: [],
  open: false,
  index: 0,

  // 此刻真的贴在悬浮窗那个 <img> 上的 URL（2026-09-09）。
  //
  // 🔴 **它和 `index` 是两件事，这正是修好的那个 bug。** `index` 是「选中第几张」，
  //    一按方向键就变；`shown` 是「屏幕上现在是哪张」，要等图片真的到了才变。
  //    原来模板直接绑 `current.src`，于是这两件事被当成了同一件 ——
  //    而它们之间隔着一次网络请求（大图中位 163KB，实测 150–400ms，弱网 1s+）。
  //    那段时间里浏览器画的是**上一张已经解码好的图**：每次点开先看到上次那张，
  //    然后跳。用户的原话是「这很莫名其妙」—— 正因为它不像加载，像点错了。
  //
  //    ⚠️ 原来模板上有一行 `x-bind:key="index"`，注释说它会让 Alpine 重建 <img>。
  //       Alpine 的 `:key` 只被 `x-for` 读，那行从 2026-08-09 进来起就没生效过。
  //       守卫补在 `core.tests.AlpineKeysBelongToXForGuardTests`。
  shown: "",

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
    this.load(index);
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
    this.load(this.index);
  },

  // 把第 i 张送上屏幕。五条，每一条挡一件具体的事（2026-09-09）。
  //
  // ⚠️ 4 和 5 是这次修复**自己引入的**问题，同属一类：为慢路径写的处理，
  //    在快路径上也照跑了一遍。占位是给「还没有的东西」准备的，而代码没问过
  //    东西在不在；等解码是为了画得不卡，而藏起来的标签页根本没有在画。
  //
  // 1. **先贴缩略图。** 它此刻已经在这一页上、已经在浏览器缓存里（和带子上那张
  //    是同一个字符串，`gallery/views.py` 那边写了为什么必须同一个），所以这一步
  //    零网络、当帧就能画。屏幕上因此永远不会出现「上一张」——
  //    过渡是「先糊后清」，而不是「先错后跳」。
  //
  // 2. **大图离屏 `decode()` 完再换。** 不用 `onload`，也不直接改 src：
  //    解码是在主线程上做的，直接换会在解码那一帧留白闪，1600px 的图在老设备上
  //    尤其明显 —— 而这一页的用户里有老年人。`decode()` 把这件事挪到屏幕外。
  //    ⚠️ 失败（网断、图坏）就**停在缩略图上**：一张略糊的对的照片，
  //       好过一个碎图标。所以 `catch` 是空的，不是忘了写。
  //
  // 3. 🔴 **`this.index === i` 这一判不能省。** 连按方向键时，先发的请求完全可能
  //    后到；不校验的话，后到的那一张会盖掉现在选中的那张 ——
  //    表现是「翻到第 5 张，屏幕上是第 3 张」。这是修 stale 图时最经典的二次 bug，
  //    而且只在网慢+手快的时候出现，本机点不出来。
  // 4. 🔴 **已经在本地的大图直接上，不要退回缩略图。** 占位是给「还没有的东西」
  //    准备的；这一张要是已经下载并解码过（往回翻一张、把同一张再点开一次、
  //    或者刚被 `prefetch()` 预热过），退回缩略图就是**把清楚的画面换成糊的**，
  //    然后再换回来 —— 白闪一下，而且什么都没等到。
  //    ⚠️ 方向键来回按正好落在这条路上：预取让相邻两张几乎总是已经在本地，
  //       所以少了这一判，「先糊后清」会在最常走的那条路上每次都演一遍。
  //    ⚠️ 判据是 `full.complete`：给 `Image` 赋过 src 之后，它为真就意味着这张图
  //       已经在浏览器缓存里、能当帧画出来。所以 `new Image()` 要排在赋值**前面**。
  load(i) {
    const photo = this.photos[i];
    if (!photo) return;

    // ⚠️ 这一张的大图已经在屏幕上了就什么都不做。方向键来回按（5→6→5）、
    //    或者把同一张关掉再点开，都会走到这里 —— 而那时候整段流程的净效果是零：
    //    `shown` 赋成它已经是的值，`decode()` 白跑一趟，`prefetch()` 把刚才那两张
    //    再预热一遍。看不见，但它落在最常走的那条路上。
    if (this.shown === photo.src) return;

    const full = new Image();
    full.src = photo.src;

    this.shown = full.complete ? photo.src : (photo.thumb || photo.src);

    // ⚠️ 这一判之内**两件事都要管**：越过它去预取，等于让一张早就没人看的
    //    照片决定现在去下载谁。连按方向键时那是两张 163KB 的无用下载，
    //    和人正在等的那张抢同一条带宽 —— 正是下面这条注释要避免的事。
    // ⚠️ `done` 让它可以被调用两次：下面有两条路都会走到这里，谁先到算谁的。
    let done = false;
    const swap = () => {
      if (done || this.index !== i) return;
      done = true;
      this.shown = photo.src;
      // ⚠️ 预取放在**这一张落地之后**，不放在前面：并行的话两张图抢同一条带宽，
      //    人正在等的那张反而更慢。
      this.prefetch(i);
    };

    // 5. 🔴 **`decode()` 没有延迟上界，所以换图不许只挂在它身上。**
    //    规范没有承诺它多久返回，而 Chrome 对**隐藏标签页**根本不跑解码管线：
    //    那个 promise 既不成功也不失败，一直挂着（2026-09-09 实测，
    //    `visibilityState === "hidden"` 时等 3 秒没有任何结果，
    //    同一张图 `fetch` 只要几毫秒）。
    //    只挂 decode 的那一版，代价是**照片永远停在 700px 被放大到 881px** ——
    //    不报错、不空白，只是一直有点糊。这个项目对这种失败有过判词：
    //    「a valid file, the right format, simply the wrong picture. Nothing
    //    raises; the lightbox is just soft forever.」
    //
    //    ⚠️ 兜底判的是**时间，不是 `document.hidden`**。隐藏只是「解码停住」的
    //       一个已知实例，不是它的定义：可见标签页上解码卡住（老机器、别的
    //       浏览器引擎、某张能解但解得慢的图）同样会掉进那个「永远糊着」，
    //       而按 `document.hidden` 写的那一版对它一点办法都没有 ——
    //       那是拿测到的那个实例当了根因。
    //    ⚠️ 计时器挂在 `load` 上，**不是**和 `decode()` 直接赛跑：`load` 早于
    //       像素解码完成，无条件赛跑等于每次换图都走没解码那条路，
    //       把 `decode()` 存在的理由整个抵消掉。挂在 `load` 上的意思是
    //       「字节已经到齐了，再给解码这么多时间，超过就直接换」。
    full.addEventListener("load", () => setTimeout(swap, DECODE_CEILING_MS));
    // ⚠️ `catch` 里补一次：decode 失败但图其实已经到了（老浏览器、某些编码），
    //    那张图是能画的，没有理由留在缩略图上。
    //
    // 🔴 **判据是两个，不是一个**（2026-09-10 更正）。这两行原来写的是「真的
    //    坏掉时 `complete` 为假，于是停在缩略图」—— **那句话是错的**，而它
    //    正好把这个分支变成了它自己要防的那件事。HTML 规范里 `complete` 为真
    //    的四种情形，第四种是「current request 的 state 是 **broken** 且没有
    //    pending request」：也就是说**加载失败会让它变成 true**。
    //
    //    真 Chrome 实测（喂一个 404 的 URL）：
    //        坏掉的图   complete=true   naturalWidth=0   → 旧写法**会换**，碎图标
    //        好图、decode 失败   complete=true   naturalWidth=1  → 两种写法都换 ✔
    //    第二行是这个修法的分寸：它没有动这个 `catch` 存在的理由，只把「坏了」
    //    和「解不出来」分开 —— 而它们在 `complete` 这一个数上长得一模一样。
    //
    //    图真的坏掉时停在缩略图 —— 一张略糊的对的照片，好过一个碎图标。
    //    ⚠️ 那种情况下 `load` 根本不会触发，所以上面那条计时器兜不到，
    //       这个 `catch` 是唯一会跑的路径。
    full.decode().then(swap).catch(() => {
      if (full.complete && full.naturalWidth > 0) swap();
    });
  },

  // 预热左右两张，只求进浏览器缓存（不解码、不上屏）。方向键连翻时，
  // 下一张通常已经在本地，第 2 步那次等待就不存在了。
  //
  // ⚠️ 只预取相邻两张，不预取整条序列：一面墙 60 张大图约 10MB，
  //    而看照片的人多半只翻几张就关掉了。
  prefetch(i) {
    const total = this.photos.length;
    if (total < 2) return;
    for (const n of [i + 1, i - 1]) {
      const photo = this.photos[(n + total) % total];
      if (photo) new Image().src = photo.src;
    }
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
// Events 那一页的外壳（2026-08-19 把散在模板里的两个布尔收拢成一个组件）
//
// 右面板是**两层**，而左边那颗开关只管底下那一层：
//
//   schedule   底层：日程画不画
//   detail     上层：右边有没有正开着一场活动（详情，或报名表单）
//   isOpen     = schedule || detail —— 决定**版面**（壳多宽、列收到多窄、
//                筛选卡钉不钉），也就是 `.is-open` 那个 class
//
// 🔴 **版面归 isOpen，不归 schedule。** 这是 2026-08-19 那批的地基：从今以后
//    点活动卡片一律在右边开，日程关着时也要把壳撑开。写成「schedule 决定版面」
//    的话，日程关着时点一张卡，面板是 `visibility: hidden` + 宽度 0 ——
//    请求发出去了、内容也换进去了，屏幕上什么都没有。
//
// ⚠️ 剩下的那一条规则用 `$watch` 而不是 `x-effect`：x-effect 会把它读到的所有
//    东西都变成依赖，于是「日程开着时点一张卡」会被同一个 effect 里的另一条
//    规则当场关掉 —— 卡片点了就没反应。$watch 只在那一个值真的变了时响。
//    （原来这里有**两条** $watch。「开日程 = 让开详情」那条 2026-08-28 搬进了
//     `showSchedule()`：翻开 `schedule` 的不再是模板里一句直接赋值，而是这个
//     方法本身，所以那条规则不必再靠监听才能听见。）
// ⚠️ 收一个初值（2026-09-09）：`?panel=<pk>` 进来时面板一开始就是开着的，而
//    模板把服务端的答案插进 `x-data="eventsShell(true)"`。
//    ⚠️ 默认值 `false` 不能省 —— 别的地方（真站上只有这一处，但守卫和将来的
//       调用方不一定）写 `x-data="eventsShell"` 时 Alpine 是**不带参数**调用它的。
// 筛选卡收起来了没有（2026-09-09）。
//
// 🔴 **为什么是 class 而不是 `x-show`。** 收起态要在 Alpine 加载**之前**就画对
//    （否则记着「收起」的人会先看到一整张 330px 的卡再看到它塌成一行），而那件事
//    只有一段内联脚本做得到 —— 它写 `<html>` 上的一个 class。若这边再用 `x-show`
//    的内联 `display`，两套机制会在同一个元素上打架：Alpine 说「显示」写的是
//    `display: ''`，而那正好把权力交回给那个 class 规则，于是展开点不动。
//    所以两边都用 class，而且**接手时立刻把 html 上那个摘掉**：任何时刻只有一套
//    在管这件事。
//
// ⚠️ 键里带 `pathname`：这张卡 Events 和管理列表两页共用，但那是两件事 ——
//    在一页收起不该把另一页也收了。键的形状和模板里那段 boot 脚本**必须一致**，
//    两处分家的表现是「收起之后刷新又回来了」。
//
// ⚠️ 读写都包在 try 里（隐私模式下 localStorage 直接抛）。读不到就当没收起 ——
//    默认展开，和这一页一直以来的样子相同。
const FILTERS_KEY = () => `filters:${window.location.pathname}`;

Alpine.data("filterCard", () => ({
  open: true,

  init() {
    const root = document.documentElement;
    // 接手：先认下 boot 脚本已经画出来的那个状态，再把它的 class 摘掉。
    // ⚠️ 顺序不能反 —— 先摘再读的话，读到的是「没收起」，卡片会当场弹开。
    this.open = !root.classList.contains("filters-collapsed");
    root.classList.remove("filters-collapsed");
  },

  // ⚠️ 一个开关，而 2026-08-28 从 `button.html` 删掉过 `toggles` —— 不冲突。
  //    当时删的理由是「一颗按钮同时是打开和关掉，而它旁边没有任何东西说明此刻
  //    按下去是哪一件」。这一颗有：箭头跟着转、`aria-expanded` 跟着变、
  //    读屏念的那句也跟着变。所以它不走那个组件，直接用共享的图标按钮长相。
  // 收起时整张卡都能点开（2026-09-09）。
  //
  // 🔴 **两道闸，缺一个就出一种毛病：**
  //    ① `!this.open` —— 展开着的时候这一层完全不管事。少了它，点一下输入框、
  //       点一下下拉、点一下 Clear，卡片自己就收起来了。
  //    ② 让开那颗箭头 —— 它自己的 `x-on:click` 先跑，然后这一下会**冒泡**到
  //       表单上。少了它，展开态点箭头是「收起，然后立刻又展开」，
  //       而屏幕上什么都不动，读起来像按钮坏了。
  //
  // ⚠️ 用 `closest()` 而不是 `event.target === button`：点中的可能是箭头里面那个
  //    `<svg>`（或者它里面的 `<path>`），那时 target 根本不是按钮本身。
  expandFromCard(event) {
    if (this.open) return;
    if (event.target.closest(".filter-toggle")) return;
    this.toggleFilters();
  },

  toggleFilters() {
    this.open = !this.open;
    try {
      localStorage.setItem(FILTERS_KEY(), this.open ? "open" : "collapsed");
    } catch (e) {
      /* A blocked localStorage costs the memory of this choice, nothing more. */
    }
  },
}));

Alpine.data("eventsShell", (openWithDetail = false) => ({
  schedule: false,
  detail: openWithDetail,

  get isOpen() {
    return this.schedule || this.detail;
  },

  init() {
    // 右边一空，左边那圈高亮就得跟着没。高亮的意思只有一个 ——
    // 「右边正开着的是这一场」—— 所以它不该活得比面板长。
    // ⚠️ 走事件而不是直接改 app.js 里那个变量：高亮归下面那一段管，
    //    而那一段不在 Alpine 的作用域里。$watch 只在真的变了时响，
    //    所以整页加载时不会白发一次。
    this.$watch("detail", (on) => {
      if (!on) this.$dispatch("panel-closed");
    });

    // 后退／前进：把面板和地址栏对齐（2026-09-09 第二批）。
    //
    // 🔴 少了这个监听，`openDetail()` push 出来的那一格按后退时**地址栏变了、
    //    面板还开着** —— 而那正是 `?panel=` 当天要修的那种「同一页上两条路给出
    //    两种结果」。
    //
    // ⚠️ 判据只读地址栏，不记自己刚才做过什么：popstate 可能来自后退、前进、
    //    或者用户手改 hash，而这三种里只有「现在的 URL 是什么」是共同的答案。
    // ⚠️ 必须幂等 —— `closeDetail()` 走 `history.back()` 时这一段会紧跟着再跑
    //    一次，那时 `detail` 已经是 false 了。
    window.addEventListener("popstate", () => {
      const pk = new URLSearchParams(window.location.search).get("panel");
      this.detail = !!pk;
      // 左边那圈高亮跟着回来。关掉那个方向由上面的 $watch 管，
      // 所以这里只说「开」——两个方向都在这里写就是把一件事说两遍。
      if (pk) this.$dispatch("panel-opened", pk);
    });
  },

  // 在面板里打开一场活动（2026-09-09 第二批）。
  //
  // 🔴 **模板上那三处 `x-on:click` 全走这里**（列表行、报名徽章、日程卡片），
  //    因为「地址栏该 push 还是该 replace」只有这里答得出来 —— 见 writePanelUrl。
  openDetail(pk) {
    this.writePanelUrl(pk);
    this.detail = true;
    this.$dispatch("panel-opened", pk);
  },

  // 「右边开着这一场」写进地址栏。
  //
  // 🔴 **从关着打开 → push；已经开着再换一场 → replace。**
  //
  //    push 那一半是给窄屏的：那一档面板铺满整屏（app.css 那条 `position: fixed`），
  //    而铺满整屏的东西，人唯一会去按的退出键是**后退**。少了它，手机上点开一场
  //    活动按后退直接离开 Events 页。
  //
  //    replace 那一半是 2026-08-19 定下、这次保留的：连点五张卡片不该在后退键上
  //    堆五层。两半合起来是一条不变量 ——
  //
  //      **任何时刻最多只有一格「面板开着」的历史记录，而且它指的一定是此刻
  //        装在 DOM 里的那一场。**
  //
  //    所以后退／前进只可能在「没有面板」和「面板＝已加载的那一场」之间移动，
  //    不存在「地址栏说 A、面板里是 B」那种状态。
  //
  // 🔴 **`panelPushed` 标的是「这一格是我们造的」，不是「有面板开着」。**
  //    两者不是同一件事：`?panel=<pk>` 直接进来的那一档（从圆球出去、再点整页
  //    那条「← Events」回来）是一次**真导航**，我们没有 push 过它 —— 对它
  //    `history.back()` 会把人送出 Events 页。所以 replace 的那一支要把当前这一格
  //    的标记**原样带过去**，不能顺手点亮。
  //
  // ⚠️ 用 `new URL(location.href)` 而不是从零拼：地址栏里可能还有筛选、页码、
  //    日程窗口 `from`，洗掉 `from` 的表现是点一张卡片、右边的日程自己跳回今天。
  // ⚠️ 包在 try 里：`history` 在少数嵌入环境里会抛（沙箱 iframe 的 SecurityError），
  //    而开不成一格历史不该把整页的 JS 带下去 —— 面板照样开，只是后退键退不掉。
  writePanelUrl(pk) {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("panel", pk);
      const here = url.pathname + url.search + url.hash;
      if (this.detail) {
        const pushed = !!(history.state && history.state.panelPushed);
        history.replaceState({ panelPushed: pushed, panel: String(pk) }, "", here);
      } else {
        history.pushState({ panelPushed: true, panel: String(pk) }, "", here);
      }
    } catch (e) {
      /* A blocked history API must not take the page down with it. */
    }
  },

  closeDetail() {
    // ⚠️ 同步翻掉，不等 popstate：`showSchedule()` 靠它立刻生效，
    //    而 `history.back()` 是异步的 —— 等它的话按下 Schedule 会有一帧
    //    两块都不显示。下面那个监听是幂等的，重复置 false 没有代价。
    this.detail = false;
    // 🔴 **把 `panel` 从地址栏里拿走**（2026-09-09）。少了这一句，「关掉」就不是
    //    关掉：刷新一下面板自己回来了，而人明明关过它。同一个地址被收藏、被转发
    //    出去也一样 —— 它会一直声称那一块是开着的。
    //
    // 🔴 **两条路，取决于这一格是不是我们 push 出来的**（2026-09-09 第二批）：
    //    · 是 → `history.back()`。这样「× 关掉」和「后退关掉」走的是同一条路，
    //      历史里不留下一条走过就没用的记录。少了这一支，push 进去那一格被
    //      replace 掉之后，后退键会先原地不动一次（同一个 URL 连着两格）。
    //    · 不是 → 照旧 `replaceState` 就地把参数摘掉。这一档是 `?panel=` 直接
    //      进来的那次真导航，back 会把人送出 Events 页。
    //
    // ⚠️ 包在 try 里：`history` 在少数嵌入环境里会抛（沙箱 iframe 的
    //    SecurityError），而关不掉一块面板不该把整页的 JS 带下去。
    try {
      if (history.state && history.state.panelPushed) {
        history.back();
        return;
      }
      const url = new URL(window.location.href);
      if (url.searchParams.has("panel")) {
        url.searchParams.delete("panel");
        history.replaceState(null, "", url.pathname + url.search + url.hash);
      }
    } catch (e) {
      /* A blocked history API must not take the page down with it. */
    }
  },

  // 「亮出日程」。⚠️ **两件事，一个方法**（2026-08-28 从一个 `$watch` 改过来）：
  //    详情压在日程上面，所以开着详情时按下 Schedule 而不让位的话，屏幕上
  //    一个像素都不动 —— 那是最糟的一种反馈。
  //
  // ⚠️ 原来这条规则写成 `$watch("schedule", on => { if (on) closeDetail() })`。
  //    那时它必须是个 watcher，因为翻开 `schedule` 的是模板里那颗开关按钮
  //    （`toggles="schedule"` 直接赋值），组件这边接不到那一下。现在按钮调的
  //    就是这个方法（button.html 的 `shows`），于是「按下 Schedule 会发生
  //    什么」整条写在一处，而不是一半在模板、一半在 watcher 里。
  //
  // 🔴 也因此它对**已经开着**的日程仍然有效：日程开着、又点了一张卡片时
  //    `schedule` 一直是 true，watcher 那一版在这里不会响 —— 再按一次
  //    Schedule 什么都不会发生，而人要的正是「把日程给我拿回来」。
  showSchedule() {
    this.schedule = true;
    this.closeDetail();
  },

  // 关掉日程（2026-08-28，配面板右上角那颗 ×）。
  // ⚠️ 关的是**底下那一层**。详情此刻不可能开着（它开着的时候日程那一块整个
  //    被 `x-show` 藏起来了，这颗 × 也就不在屏幕上），所以这里不碰 `detail` ——
  //    顺手把它一起关掉的话，「关掉日程」就悄悄多了一个意思。
  closeSchedule() {
    this.schedule = false;
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

// 返回「定位成功了没有」—— 调用方靠它决定要不要再等一次。
function scrollScheduleIntoView(root) {
  const scroller = root.querySelector("[data-schedule-scroll]");
  if (!scroller) return false;
  // 🔴 **面板关着的时候什么都别做。** 关着时它是 `flex: 0 0 0` + `visibility:
  //    hidden`，可视高度为 0，于是下面那个 `scrollHeight - clientHeight` 是 0，
  //    夹完之后 scrollTop 一定是 0 —— 日程打开时停在凌晨，一天里空的那一段，
  //    看起来像「日程里什么都没有」。
  //
  //    ⚠️ 而默认就是关着的，所以这条路是**每一次真实页面加载**都会走的那条。
  //       我第一次验证时把日程改成了默认打开（为了截图方便），正好绕开它 ——
  //       测试装置把 bug 藏了。
  if (!scroller.clientHeight) return false;
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
  return true;
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
  paintSchedule(root);
  if (scrollScheduleIntoView(root)) {
    root.dataset.scheduleLive = "1";
    return;
  }

  // 面板还关着（零尺寸）。⚠️ **不能就此作罢，也不能标记成已定位** ——
  // 默认就是关着的，标记了就等于「日程永远停在凌晨」。等它第一次张开。
  //
  // ⚠️ 用 ResizeObserver 而不是监听那颗按钮：张开是 Alpine 翻一个 class、
  //    再由 560ms 的过渡把宽度撑开的，按钮按下的那一刻高度还是 0。
  //    盯尺寸才是盯到「真的能滚了」这件事本身。
  const scroller = root.querySelector("[data-schedule-scroll]");
  if (!scroller || typeof ResizeObserver === "undefined") return;
  const waiting = new ResizeObserver(() => {
    // ⚠️ 翻页会把整块日程换掉，旧的那棵树就此脱离文档 —— 不在这里断开的话，
    //    每翻一页留下一个永远等不到张开的 observer。
    if (!root.isConnected) {
      waiting.disconnect();
      return;
    }
    if (scrollScheduleIntoView(root)) {
      root.dataset.scheduleLive = "1";
      waiting.disconnect();
    }
  });
  waiting.observe(scroller);
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
// 面板上那颗圆球钉在哪儿：实测面板的位置，写成两个变量（2026-09-08）
//
// 🔴 **要同时满足两件互相打架的事**：球要浮在**右面板**上（用户第三次的原话就是
//    「悬浮在右边面板」），而且**不许滚一下才看得见**。
//
//    纯 CSS 做不到，原因是面板自己的一条老性质：它 `sticky` 之后的位置是
//    `top: --head-h + 1rem`（56），而**没吸住之前**自然位置在标题行下面
//    （量到 188），可 `height` 是按吸住之后那一档算的（`100svh - --head-h - 2rem`）。
//    于是 `scrollY = 0` 时面板下沿在视口下面约 132px —— 一颗 `absolute` 钉在
//    面板下沿的球就落在视口外面。而 `fixed` 到视口右下角的那一版（试过）球会落在
//    面板**外面**的页面底色上，读起来是一颗全局按钮。
//
// ⭐ 所以球是 `fixed`，而它的两个偏移量**由这里实测**：
//      · 横向 —— 面板右沿往里 16px，于是它永远压在面板上；
//      · 纵向 —— 面板下沿往上 16px，**但不许低于视口下沿 16px**（那个 `Math.max`
//        就是全部机关）。面板下沿在屏幕里时球贴着面板的角，面板垂到屏幕外时
//        球停在视口底上。
//
// ⚠️ 不写死一个数、也不在 CSS 里把外壳那套几何（62rem/78rem 两档宽度加负外边距）
//    再推一遍：那就是把同一份布局算两遍，而分家的表现是某个宽度上球飘在面板外面。
//    ⚠️ 同一条道理 2026-08-19 曾经由 `--panel-fits` 那段注释代言，而那个变量
//       2026-09-09 删了（面板在任何宽度都就地开）—— 道理留着，例子换成这一条。
//
// ⚠️ 没有 JS 时退回 CSS 里的默认值（视口右下角 1rem）。球仍然看得见、点得动、
//    通向同一页 —— 只是不压在面板上。这是知情的降级，不是坏掉。
//
// ⚠️ `scroll` 必须监听：面板是 sticky 的，它在视口里的位置跟着滚动变。
//    每帧最多算一次（rAF 合并），而这一段只读 `getBoundingClientRect()`、
//    不改任何布局，所以不会自己制造回流风暴。
//
// ⚠️ ResizeObserver 盯的是**面板**：开关日程那 560ms 的过渡里它的宽度一直在变，
//    而那个过程根本不触发 `resize` —— 同 `--filter-h` 那一段踩过的坑。
function watchExpandBall() {
  const shell = document.querySelector(".events-shell");
  const panel = document.querySelector(".schedule-panel");
  if (!shell || !panel) return;

  const GAP = 16;
  let queued = false;
  // 上一次真的写进去的两个值。⚠️ 不是优化洁癖：滚动过程中这两个数**几乎从不变**
  //    （面板吸住之后 `box.bottom` 相对视口是定的，吸住之前 `Math.max` 把它钳成
  //    一个常数），而自定义属性是**继承**的 —— 写在外壳上就等于每帧让整页
  //    （二十行活动加一整块日程）重新算一次样式，为了两个一模一样的数。
  let written = "";

  const place = () => {
    queued = false;
    // 🔴 面板关着的时候整段不做事。球长在 `#schedule-detail` 里，而那一块默认是
    //    空的、且被 `x-show` 藏着 —— 但 `.schedule-panel` 一直在 DOM 里
    //    （关着时是 `visibility: hidden`）。不挡这一下的话，**每一个打开
    //    /events/ 却从不开面板的人**（也就是默认视图）每滚一帧都要为一个不存在
    //    的球做一次强制重排。这一行读的是 class，不碰布局。
    if (!shell.classList.contains("is-open")) return;
    const box = panel.getBoundingClientRect();
    // 🔴 那两个 `Math.max` 就是「不用往下滚也看得见」的全部实现。
    const right = Math.round(Math.max(GAP, window.innerWidth - box.right + GAP));
    const bottom = Math.round(Math.max(GAP, window.innerHeight - box.bottom + GAP));
    const now = `${right} ${bottom}`;
    if (now === written) return;
    written = now;
    shell.style.setProperty("--panel-fab-right", `${right}px`);
    shell.style.setProperty("--panel-fab-bottom", `${bottom}px`);
  };

  const soon = () => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(place);
  };

  window.addEventListener("scroll", soon, { passive: true });
  // ⚠️ `resize` 留着是给没有 ResizeObserver 的浏览器兜底 —— 有它的时候下面那个
  //    观察器已经覆盖了拖窗口这件事（面板的宽高都是视口推出来的）。
  window.addEventListener("resize", soon);
  // ⚠️ **不监听 `htmx:afterSettle`。** 换进面板的是 `#schedule-detail` 的
  //    innerHTML，而这两个数量的是 `.schedule-panel` 的盒子 —— 那个盒子在
  //    任何一次 swap 里都不变，写在外壳上的变量也活得下来。挂上去的代价是
  //    全站每一次落地（而它一次请求会响**两下**，见 `startSchedule()` 那条注释）
  //    都白排一帧。开关面板那 560ms 的过渡里盒子确实在变，那件事归下面那个观察器。
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(soon).observe(panel);
  place();
}

watchExpandBall();

// ---------------------------------------------------------------------------
// 页头条：钉住了没有（2026-08-28）
//
// 静息态那一行是**透明**的 —— 底由站头那块向下延的面板画，这样两行在屏幕上是
// 一整块（深色 + 大图那一档里尤其重要：两块毛玻璃各取各的背景，接缝处是一道
// 看得见的亮度台阶）。而钉住之后那块面板已经滚走，这一行底下正有活动卡片穿过
// 去，它必须自己有底，否则就是**字压着字**。
//
// ⚠️ 判据来自哨兵（页头条在流里那个位置上的一个零高度元素）**离没离开视口顶**，
//    不是 `scrollY > 某个数`：那个数是「站头有多高」，而它两档不同、还会随字体
//    和窄屏换行变 —— 写死一个就会在某一档上早一点或晚一点翻。
//
// ⚠️ IntersectionObserver 而不是 scroll 监听：后者要么掉帧、要么得自己节流，
//    而这件事只需要在**越过那一条线**的那一帧知道一次。
//
// ⚠️ 交接是严丝合缝的：那块面板向下延的高度**正好等于**这一行，所以它从视口里
//    退出去的那一帧，正是哨兵离开的那一帧。两处的数来自同一个变量
//    （`--page-bar-h` / `--page-bar-pull`，见 app.css）。
function watchPageBar() {
  const bar = document.querySelector(".page-bar");
  const sentinel = document.querySelector(".page-bar-sentinel");
  if (!bar || !sentinel || typeof IntersectionObserver === "undefined") return;
  new IntersectionObserver(
    ([entry]) => bar.classList.toggle("is-stuck", !entry.isIntersecting),
  ).observe(sentinel);
}

watchPageBar();

// ---------------------------------------------------------------------------
// 行内「⋯」菜单落在触发器旁边（2026-09-03 设计评审第 3 + 6 条）
//
// ⭐ **纯增强。** 开合、点外面关、Esc 关全是 `popovertarget` 给的，一行 JS 都不需要
//    （D24：菜单里装的是这一行仅有的几个写操作，不能只有 JS 一条路）。
//    这段只管**位置**：没有它，popover 落在规范的默认锚定 —— 视口正中央 ——
//    菜单照样能开、能提交，只是位置在屏幕中间。
//
// ⚠️ 用 `beforetoggle` 而不是 `toggle`：要在它**画出来之前**把坐标写上去，
//    否则会看到它先在屏幕中间闪一下再跳到旁边。
//
// ⚠️ 坐标是 `position: fixed` 的坐标，所以直接用 `getBoundingClientRect()`，
//    不加 scrollY —— top layer 里的元素不跟着页面滚。
//    ⚠️ 也正因为它不跟着滚，滚动时菜单会留在原地。浏览器对 `popover=auto` 的
//       处理是留着（不像 `<select>` 会跟随），而这里可以接受：菜单一开就是要
//       马上点的东西，而滚动本身不是关它的手势。
//
// ⚠️ 右边和下边都要兜底：最后一列的菜单往左够不到边就贴右缘，
//    最后几行的菜单往下放不下就翻到触发器上方。少了这两句，表格最右下角那一行
//    的菜单有一半在视口外 —— 而那一行恰恰是最常出现的位置（人滚到底再操作）。
function positionRowMenus() {
  const GAP = 6;
  const FALLBACK_WIDTH = 192;          // .row-menu 的 min-width（12rem）

  const place = (panel) => {
    const trigger = document.querySelector(`[popovertarget="${panel.id}"]`);
    if (!trigger) return;
    const at = trigger.getBoundingClientRect();
    const box = panel.getBoundingClientRect();
    const width = box.width || FALLBACK_WIDTH;

    let left = at.right - width;
    if (left + width > window.innerWidth - GAP) left = window.innerWidth - width - GAP;
    if (left < GAP) left = GAP;

    // ⚠️ 只有量得到高度时才翻面（见下面 beforetoggle / toggle 两段的分工）。
    let top = at.bottom + GAP;
    if (box.height && top + box.height > window.innerHeight - GAP) {
      top = at.top - box.height - GAP;
    }
    if (top < GAP) top = GAP;

    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  };

  const mine = (event) => {
    const panel = event.target;
    return panel?.classList?.contains("row-menu") ? panel : null;
  };

  // 🔴 **第三个参数 `true`（捕获阶段），少了它这段一次都不会跑。**
  //
  //    `beforetoggle` / `toggle` 在 popover 上**不冒泡**（ToggleEvent 的
  //    `bubbles` 是 false），所以挂在 document 上的普通监听器永远收不到。
  //    而**捕获阶段对不冒泡的事件照样走**（window → target 那一趟总是发生）。
  //
  //    ⚠️ 踩过：第一版没写这个 true，表现是菜单能开、能点、什么都不报错，
  //       只是永远落在视口左上角 (0,0) —— 因为 `inset: auto` 已经把
  //       UA 的居中清掉了，而没有人写 top/left。浏览器里看出来的。
  document.addEventListener("beforetoggle", (event) => {
    if (event.newState === "open") {
      const panel = mine(event);
      if (panel) place(panel);
    }
  }, true);

  // ⚠️ 两段分工：`beforetoggle` 时面板还是 `display: none`，**量不到高度**，
  //    所以那一次只定左右和「放在触发器下面」；等 `toggle`（已经显示）再量一次，
  //    需要的话翻到上面去。常见情况下两次算出同一个值，屏幕上没有位移。
  //    ⚠️ 少了第二段，表格最后几行的菜单会有一半在视口外 —— 而那恰恰是最常
  //       操作的位置（人滚到底再动手）。
  document.addEventListener("toggle", (event) => {
    if (event.newState === "open") {
      const panel = mine(event);
      if (panel) place(panel);
    }
  }, true);

  // 🔴 **开着的时候还要跟着滚**（2026-09-04 修）。
  //
  //    `.row-menu` 是 `position: fixed`，坐标是开的那一刻按触发器算出来的一对
  //    视口坐标 —— 而 popover 不会因为滚动而关闭。于是开着菜单再滚一下，
  //    面板钉在原地、它那一行走掉了，屏幕上它**贴在了另一行旁边**。
  //    ⚠️ 这不只是难看：这张表里的动作是 Take down / Back to draft，
  //       一个看起来指着隔壁行的菜单，是会让人对着错的那一行点下去的。
  //       （点下去仍然作用在正确的那一行 —— 表单里是它自己的 pk ——
  //       所以这个毛病不会有任何报错，只会让人以为自己点错了。）
  //
  // ⚠️ `capture: true`：滚动事件在元素上**不冒泡**，而这张表自己就是一个滚动
  //    容器（`.table-wrap` 是 `overflow-x: auto`）。少了它，只有整页滚动会被
  //    接住，表格内部横向滚动时面板照样掉队。
  //
  // ⚠️ `passive: true`：这两个监听器一行都不碰事件，声明出来浏览器才不用等
  //    它们决定要不要 preventDefault —— 滚动手感的差别就在这里。
  const followOpenMenus = () => {
    document.querySelectorAll(".row-menu").forEach((panel) => {
      if (panel.matches(":popover-open")) place(panel);
    });
  };
  window.addEventListener("scroll", followOpenMenus, {passive: true, capture: true});
  window.addEventListener("resize", followOpenMenus, {passive: true});
}

positionRowMenus();

// ---------------------------------------------------------------------------
// 「Clear」：清筛选，别的什么都不动（2026-08-28）
//
// 🔴 这颗按钮此前是一个普通链接，点下去**整页重新加载** —— 于是右边正开着的
//    活动详情/报名表单一起没了，日程也退回今天。用户报上来的原话是
//    「点 clear 会直接关掉右边面板？Clear 只是取消 filter，不应该关任何右边
//    面板」。整页重来从来不在这颗按钮的意思里，它只是当时最省事的实现。
//
// 做法是「清空字段 + 触发这张表单本来就有的那一次请求」，三个后果都是要的：
//   · 面板（详情 / 报名 / 日程）一个字都没碰 —— 这段代码里没有它们的名字；
//   · 筛选卡里四个框当场空掉，屏幕和列表说的是同一件事；
//   · `hx-replace-url` 照旧把地址栏改成清空后的那一份，可收藏、可转发。
//
// ⚠️ **只清看得见的字段。** 隐藏字段是上下文，不是筛选：`scope`（我在看谁的
//    活动）留着，理由写在 `_period_filter.html`；`from`（日程翻到了哪一天）
//    也留着 —— 清掉它等于「点 Clear 把日程弹回今天」，正是这次要修的那类事。
//
// ⚠️ 用事件委托挂在 document 上，不是在每颗按钮上挂一个：筛选卡本身不会被
//    HTMX 换掉（它在 `#event-results` 外面），但管理列表那一页的整块结果区会，
//    而委托对新换进来的节点天然有效。
//
// ⚠️ `htmx.trigger(form, "submit")` 发的是一个**自定义事件**，不是浏览器的
//    表单提交：表单上写着 `hx-trigger="input delay:400ms, submit"`，htmx 听到
//    它就发那次 hx-get。派发一个不可信的 submit 事件不会让浏览器自己去提交，
//    所以这里不会变成一次整页跳转。
//
// ⚠️ 这个包**没有**往 window 上挂自己（见文件顶上那行 import）：写
//    `window.htmx.trigger(...)` 的话这里会静默地什么都不做，而 `href` 已经被
//    `preventDefault()` 拦掉了 —— 表现是「点 Clear 完全没反应」。
//
// ⚠️ 这段脚本整个没加载出来时**什么都不拦**：`href` 还在，浏览器照常跳到
//    清空后的那一页（D24 的渐进增强）。这也是为什么拦截写在这里，而不是模板里
//    一句 `x-on:click.prevent` —— 那一句在 Alpine 没起来时会变成一颗死按钮。
document.addEventListener("click", (event) => {
  const link = event.target.closest?.("[data-clear-filters]");
  if (!link) return;
  const form = link.closest("form");
  if (!form) return;

  event.preventDefault();
  for (const field of form.querySelectorAll("input, select, textarea")) {
    // ⚠️ 判据是 `type === "hidden"`，不是「看不看得见」：一个被 CSS 藏起来的
    //    真筛选字段仍然该被清掉，而一个隐藏字段就算画出来了也仍然是上下文。
    if (field.type !== "hidden") field.value = "";
  }
  htmx.trigger(form, "submit");
});

// ---------------------------------------------------------------------------
// 🔴 **这里曾经有一个 `panelFits()`（2026-08-19 – 2026-09-09）。别把它加回来。**
//
// 它读 app.css 在 `@media (width >= 64rem)` 里点亮的 `--panel-fits`，供活动卡片
// 上 `hx-trigger="click[panelFits()]"` 那个事件过滤器分岔：宽屏就地开面板，
// 窄屏放手让浏览器跟着 href 跳整页。
//
// 那套双行为**从第一天起就不成立**，因为 htmx 对带 href 的 `<a>` 是
// `preventDefault()` 在前、问事件过滤器在后（`htmx.js` 的 `shouldCancel`）。
// 窄屏上的实际结果是：跳页被打掉、请求没发、面板照开 —— 一块铺满整屏的空白。
// 全部经过写在 `_event_list_results.html` 那一段注释里。
//
// 现在面板在任何宽度都就地开，于是**这个文件不需要知道屏幕多宽** ——
// 断点退回成纯粹的版面问题，只活在 app.css 里。
// 守卫：events.tests.EventsShellStateTests
//       .test_the_breakpoint_is_declared_once_in_the_stylesheet

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

// ⚠️ 服务端画出来的那一圈高亮要认回来（2026-09-09）。`?panel=<pk>` 进来时
//    `is-picked` 已经在某一行上了，而这个变量还是 null —— 于是关掉面板时
//    `paintPicked()` 不知道该清谁，那一圈会一直亮着，指向一块已经关掉的面板。
//    ⚠️ 从 DOM 认，不再从服务端插一个值进 JS：那一行上的 class 已经是答案，
//       插第二份就是同一件事的两个来源。
{
  const already = document.querySelector(".event-row.is-picked");
  if (already) pickedEvent = already.dataset.event;
}

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

// 顶上钉着的那一条挡掉多少（2026-08-28 改成实测）。
//
// 🔴 **量它，不要猜它是哪一条。** 页面顶上钉着的可能是站头那条 bar（68/76px），
//    也可能是页头条（52px，活动列表就是这一档，站头那条跟着内容滚走了）——
//    而这里只需要一个数：`getBoundingClientRect().bottom`，也就是「视口顶到
//    它下沿」。钉住的东西这个数就是它的高度，没钉住的（滚走了）这个数是负的，
//    于是自然退回下面那个兜底。
//
// ⚠️ 兜底 76px 是站头那两档里**高**的一档：判「看得见吗」时宁可保守 ——
//    多滚一次的代价，远小于「明明被盖住却判成可见」（那时人看到的是
//    「有高亮，却找不到那一行」）。
// ⚠️ 不去读 `--head-h`：那是 rem，换算要知道根字号，而根字号是可以被用户改的。
//    量出来的像素没有这一层假设。
const TOP_BAR_PX = 76;

function pinnedHeadBottom() {
  // ⚠️ 两次 querySelector，**不是** `.page-bar, .site-head` 一次：分组选择器返回
  //    的是**文档顺序**里的第一个，而站头在页头条前面 —— 于是有页头条的页面上
  //    永远量到那条已经不吸顶的 bar，一路退回 76px 的兜底。屏幕上只是「滚动
  //    多让开了 24px」，没有任何报错。
  const bar = document.querySelector(".page-bar")
    || document.querySelector(".site-head");
  if (!bar) return TOP_BAR_PX;
  if (getComputedStyle(bar).position !== "sticky") return TOP_BAR_PX;
  return Math.max(0, bar.getBoundingClientRect().bottom);
}

// 视口顶上**一共**被挡住多少 —— 顶栏，加上钉住的筛选卡（如果它此刻钉着）。
//
// 🔴 少算筛选卡这一截，就是用户报上来的那个 bug：「点日程里的卡片，左边有高亮，
//    但卡片藏在 filter 卡片下面」。右面板开着时筛选卡是 `position: sticky`，
//    约 257px 高；只让开顶栏的话，一行落在它底下会被判成「完整可见」，于是
//    下面那句 `return` 直接不滚了 —— 高亮画上了，人却看不见那一行。
//
// ⚠️ 高度**实测**，不写死：这张卡窄屏上控件换行、表单报错时多一行字，高度会变。
//    （同一个理由，app.css 那条 `scroll-margin-top` 用的也是实测的 `--filter-h`。）
// ⚠️ 判「钉着没有」看计算出来的 `position`，不看外壳有没有 `.is-open` ——
//    钉不钉是样式表的决定（`.events-shell.is-open .filter-card`），
//    在这里重述一遍那个条件就是第二个会漂移的答案。
function occludedTop() {
  const head = pinnedHeadBottom();
  const card = document.querySelector(".filter-card");
  if (card && getComputedStyle(card).position === "sticky") {
    return Math.max(head, card.getBoundingClientRect().bottom);
  }
  return head;
}

// 🔴 **只有那一行不完整可见时才滚。**（2026-08-19 修）
//
//    这个函数原本是给「从日程点过来」写的：那一场可能在列表的另一页、
//    在屏幕外，不滚过去的话左边看起来毫无反应。而 2026-08-19 给左边那些行
//    也加上 `data-event` 之后，**从左边点也走这条路** —— 于是点一个就在光标
//    底下的卡片，页面还要平滑滚几百像素把它挪到正中。
//
//    线上量到的：数据 ~175ms 就到位，页面**滑到 1143ms 才停**（772px）。
//    连点时每一下都从头开始滑，所以越点越像卡死。用户报上来的原话是
//    「不停点不同的卡片，整个东西特别特别慢，竟然要等 1 秒钟」——
//    等的不是数据，是这段动画。
//
// ⚠️ 判据写成「不完整可见」而不是「点击来自哪一边」：一条规则同时管住两个
//    入口，不需要在别处记住这次点击是从哪儿来的。从左边点的必然可见（人就是
//    照着它点的），从日程点的可能不可见 —— 两种情形各自得到对的行为。
function isFullyVisible(row) {
  const box = row.getBoundingClientRect();
  return box.top >= occludedTop()
    && box.bottom <= (window.innerHeight || document.documentElement.clientHeight);
}

function scrollPickedIntoView() {
  if (pickedEvent === null) return;
  const row = document.querySelector(
    `.event-row[data-event="${CSS.escape(String(pickedEvent))}"]`);
  // ⚠️ 找不到是**正常**的：那一场可能不在左边的列表里（日程画的是那几天的
  //    全部，列表还带着「今天起」那一刀），窄屏上那一整列更是 display:none。
  //    静静地什么都不做，不是报错。
  if (!row || !row.offsetParent) return;
  if (isFullyVisible(row)) return;
  // 滚到「顶上被挡住的那一截**下面**」，而不是 `scrollIntoView` 的 center。
  //
  // ⚠️ center 不够：它把行放在视口正中，而钉住的筛选卡在开着的时候能占到 333px ——
  //    窗口矮一点（比如 600px 高，正中是 300px）时那一行仍然压在卡片底下。
  //    ⚠️ 也不能用 `block: "start"`：那会把行顶到视口最上面，正好被顶栏盖住 ——
  //       这是原来选 center 想躲开的那件事。现在两件一起躲开：明确地滚到
  //       「第一处没有被挡住的位置」。
  const gap = 12;
  window.scrollBy({
    top: row.getBoundingClientRect().top - occludedTop() - gap,
    behavior: "smooth",
  });
}

// 记下右边正开着的是谁。
//
// 🔴 **听的是 `panel-opened`，不是点击**（2026-09-09 第二批改过来的）。
//    原来这里委托在 body 上听 `[data-event]` 的点击 —— 那时「打开面板」这件事
//    只可能由一次点击引起，所以两者等价。现在不等价了：**后退／前进也会打开
//    面板**（见 eventsShell 的 popstate）。听点击的话，前进键把面板开回来时
//    左边那一圈高亮不会跟着回来 —— 面板开着，而没有任何一行说自己是它。
//
// ⚠️ 事件由 `openDetail()` 和那个 popstate 监听一起发，`event.detail` 是 pk。
//    和下面那条 `panel-closed` 正好配成一对：开和关是同一件事的两个方向，
//    所以它们的来源也该是同一个。
document.body.addEventListener("panel-opened", (event) => {
  pickedEvent = event.detail;
  paintPicked();
});

// 右面板关掉了 —— 高亮跟着没（2026-08-19）。
//
// 🔴 高亮的意思**只有一个**：右边正开着的是这一场。所以它不能活得比面板长。
//    此前没有任何地方清过它，表现是：关掉日程之后左边还圈着一行，而右边
//    什么都没开着 —— 一个指向空处的记号。
//
// ⚠️ 事件由外壳那个 Alpine 组件在 `detail` 变假时发出（见上面 eventsShell）。
//    在这里监听而不是让 Alpine 直接改 `pickedEvent`：这个变量归这一段管，
//    而 Alpine 的作用域够不到它。
document.body.addEventListener("panel-closed", () => {
  pickedEvent = null;
  paintPicked();
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

// --- 管理列表：鼠标停在 When 那一格某一行上时，那个写着星期几的小窗 --------
//
// 那一格两行写的是「Aug 30, 2026, 9am」—— 月日年时刻，没有星期几。星期几对
// 排班的人有用（「哦这场是周日」），但写进那一行会让这一列再宽出约 2.5rem，
// 而这一整批要修的正是「每个 event 都太长了」。于是它进了这个小窗。
//
// ⭐ **这不是这一批之前那个同名的小窗。** 那一个显示的是完整的起止时刻 ——
//    也就是把**主信息**藏进悬停，而它在触屏上不装，手机用户什么都拿不到。
//    这一个显示的只是星期几：日期本身就在屏幕上。补充信息只给鼠标是知情的
//    取舍，主信息不是。读屏用户走的是每一行里那段 `sr-only`。
//
// ⚠️ **这一段只搬字和算坐标，一个日期都不算。** 星期几由服务端渲染进
//    `data-dow`（events/schedule.py 的 `_when_line`）—— 页面的时区是基金会的，
//    而浏览器的时区是访客的。在这里从时间戳算的话，一个在纽约的 foundation
//    admin 会看到周日被写成周六，而且不报任何错。
//
// ⚠️ 整段包在一个立即执行的箭头函数里，不是一个裸的块 —— 下面那句提前退出的
//    `return` 需要一个函数体。裸块里写 `return` 是语法错误，而 esbuild 会把
//    整个 bundle 一起报废，不只是这一段。
(() => {
  // 🔴 **触屏上整段不装。** 移动端浏览器点一下也会派发 mouseover，不挡的话
  //    点一下日期会先弹出一个跟手指无关的小盒子。那一档看到的是行里那两个
  //    完整日期本身，而星期几对他们是缺的 —— 这是知情的取舍，不是疏忽。
  //    ⚠️ 用 `(hover: hover)` 而不是判 UA：能不能悬停是设备的能力，不是牌子。
  if (!window.matchMedia("(hover: hover)").matches) return;

  const GAP = 6;   // 小窗和那一行之间让开的距离
  const EDGE = 8;  // 视口边缘留的余量

  // ⚠️ 每次都重新 `getElementById`，不缓存。这一页整块 `#event-results` 会被
  //    筛选 / 翻页 / 改状态换掉，而小窗在那块**外面**，所以它其实活得下来 ——
  //    但缓存一个可能为 null 的引用意味着这段代码在别的页面上（那里根本没有
  //    这个元素）也要各处判空。取一次便宜。
  const boxOf = () => document.getElementById("when-dow");

  function hide() {
    const box = boxOf();
    // ⚠️ 用 `hidden` 而不是 `style.display`：`[hidden]` 是元素自己的状态，
    //    不和样式表抢那个属性。
    if (box) box.hidden = true;
  }

  // ⚠️ 位置锚在**那一行自己**身上，不跟着光标走。上一版跟光标是因为它显示的是
  //    主信息、要一直贴在手边；这一个只有一个词，跟着光标飘反而在一列日期上
  //    不停抖。
  //
  // 🔴 **摆在那一行的右边，不是下边。** 下边那个位置上正好是这一格的另一行
  //    （Starts 底下就是 Ends）—— 而这两行是一对，解释其中一个的时候盖住另一个
  //    是最糟的落点。浏览器里实拍到过。右边是这一格自己的空白，宽度不够时
  //    翻到左边。
  //
  // ⚠️ 坐标直接用 client 系，因为小窗是 `position: fixed` —— 它的包含块就是
  //    视口，所以这里**不需要**加 scrollX / scrollY。加了的表现是：页面滚到
  //    下半部分之后，小窗掉到屏幕外面去。
  function show(line) {
    const box = boxOf();
    if (!box) return;
    box.textContent = line.dataset.dow || "";
    // ⚠️ 先显示再量尺寸：`[hidden]` 的元素 `getBoundingClientRect()` 全是 0，
    //    于是下面那两处「够不够地方」的判断永远答「够」，贴着右下角时小窗
    //    会被裁掉一条。摆位在同一帧里完成，所以看不到它在旧坐标上闪。
    box.hidden = false;
    const anchor = line.getBoundingClientRect();
    const self = box.getBoundingClientRect();
    // 右边放不下时翻到左边 —— 硬夹在视口右缘的话它会横着压在这一行上。
    const right = anchor.right + GAP;
    const left = right + self.width + EDGE > window.innerWidth
      ? anchor.left - GAP - self.width
      : right;
    // 和这一行垂直居中。⚠️ 用 `anchor.top + anchor.height / 2` 而不是
    //    `anchor.bottom`：这一行的高度就是一行字，居中之后小窗和它是一条基线上
    //    的两个东西，而不是一个吊在下面的挂件。
    const top = anchor.top + (anchor.height - self.height) / 2;
    box.style.left = `${Math.max(EDGE, left)}px`;
    box.style.top = `${Math.min(Math.max(EDGE, top), window.innerHeight - self.height - EDGE)}px`;
  }

  // ⚠️ 委托挂在 document 上。`#event-results` 每一次筛选 / 翻页 / 改状态都会被
  //    整块换掉，挂在行上或者表上的监听会跟着一起没 —— 而表现是「筛一次之后
  //    小窗就不出来了」，控制台一声不吭。
  //
  // ⚠️ 找的是 `[data-dow]`，也就是 When 那一格里的**某一行**，不是整格：
  //    两行是两个不同的日期，很可能是两个不同的星期几。
  document.addEventListener("mouseover", (event) => {
    const line = event.target.closest?.("[data-dow]");
    if (line) show(line);
  });

  // ⚠️ `mouseout` 也要判一次 closest：那一行里还套着 `.when-label` 和 `.sr-only`
  //    两个 span，在它们之间移动同样会派发 mouseout，而那时光标并没有离开这一行。
  document.addEventListener("mouseout", (event) => {
    if (event.target.closest?.("[data-dow]")) hide();
  });

  // 🔴 **换掉那一块之前先收起来。** 改一个状态会把整块 `#event-results` 换成
  //    新的 DOM，而光标底下那一行会被一起替换掉 —— 于是 `mouseout` 永远不会来，
  //    小窗就那么钉在屏幕上，直到下一次悬停。
  document.body.addEventListener("htmx:beforeSwap", hide);
})();
