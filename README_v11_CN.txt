俄罗斯旅行浏览器插件智能导入工具包 v11（重要信息去重修正版）

这一版沿用英文文件夹/文件名，避免 Windows 解压时中文路径乱码或显示为空。

包含文件：
1. travel_page_capture_extension/  浏览器插件文件夹
   - manifest.json
   - popup.html
   - popup.js
2. travel_plugin_import_server_v11.py  本地接收与识别服务
3. russia_travel_calendar_v11.html  插件接收版 HTML 页面

使用步骤：
1. 打开 PowerShell，进入本文件夹：
   cd 你的解压路径ussia_travel_toolkit_v11_notes_dedup

2. 启动本地服务：
   python travel_plugin_import_server_v11.py

3. 打开 Chrome / Edge 扩展程序页面，开启“开发者模式”，点击“加载已解压的扩展程序”，选择：
   travel_page_capture_extension

4. 双击打开：
   russia_travel_calendar_v11.html

5. 在 Ozon / 酒店 / 火车网页点击插件读取当前页面，再回到 HTML 页面点击读取插件导入、高级识别预览、加入候选酒店对比。

v11 重点修正：
- 备注栏优先采用插件正文最后完整的“此外”弹窗内容。
- 不再把“重要信息”短版提示和“此外”完整版重复拼接。
- 报到/离开时间继续作为独立列，不重复写入备注。
- 其余 v10 功能保留：到店/离店独立列、城市筛选、候选表列排序、Ozon 当前日期价格识别、火车站/机场距离拆分、市中心距离评分。
