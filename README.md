# tv-sources

给海外电视 App 用的直播源清单，由 GitHub Actions 每 6 小时自动更新：

- `candidates.json`：候选源与过滤规则（手工维护，增删源改这里）。
- `scripts/build.py`：拉取候选源 → 过滤 → 实测线路 → 只保留当天能播的 → 生成 `live/*.m3u` 和 `sources.json`。
- `sources.json`：App 启动时读取的源清单（自动生成，不要手改）。

本仓库只保存第三方公开列表里的链接，不托管任何视频内容。
