---
name: wechat-miniprogram-development
description: 在微信小程序实现、联调或验收时使用，覆盖页面路由、接口接入、开发者工具预览和真机验证。
---

# 微信小程序开发

1. 先核对项目用原生小程序还是跨端框架，确认项目入口、构建产物目录、`project.config.json`、页面路由和目标基础库。沿用已有工程结构和构建命令，用独立的目标项目工作区开发。
2. 按跨端契约接入后端。小程序复用商品、购物车和订单接口的业务语义。将平台登录、网络请求和模拟支付封装在适配层，让测试能切换 Mock。涉及微信登录时，将 `wx.login` 返回的凭证交给服务端处理，平台密钥只放在服务端。
3. 围绕用户主路径验证加载、空数据、弱网失败、重试和返回页面后的状态。电商场景覆盖商品详情到下单、模拟支付结果、重复点击提交和订单恢复。预览用测试数据，真实支付、上传版本和发布按任务授权执行。
4. 运行项目的构建和测试，再用已授权的微信开发者工具编译预览，检查路由、控制台和接口请求。涉及设备行为时在可用的 iOS 和 Android 真机验证，记录设备和基础库版本。若环境仅能构建或静态检查，交付已验证范围和待真机验证项。
5. 交付构建命令、预览入口或进入步骤、测试数据、逐项结果及截图或日志。只读 QA 独立复验并记录环境限制。

## 参考来源

- 工程配置、路由和微信开发者工具预览。 [TencentCloudBase · Miniprogram Development](https://github.com/TencentCloudBase/skills/blob/main/skills/miniprogram-development/SKILL.md)（[MIT](https://github.com/TencentCloudBase/skills/blob/main/LICENSE)）
- 小程序接口与平台行为。 [微信开放文档](https://developers.weixin.qq.com/miniprogram/dev/framework/)
