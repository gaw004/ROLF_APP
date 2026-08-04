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
