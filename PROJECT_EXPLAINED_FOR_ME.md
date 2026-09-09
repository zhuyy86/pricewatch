# PriceWatch：给我自己的通俗讲解

## 项目解决什么问题

普通网页采集器只会告诉用户“这次抓到了什么”。PriceWatch 还会回答“这次和上次相比发生了什么”，把商品快照存进 SQLite，并输出价格、库存、新增和消失事件。

## 用户怎么使用

用户在 YAML 中填写网页地址和 CSS Selector，然后运行命令。程序继续生成原有 CSV；如果提供 `--database` 和 `--report`，还会保存历史并生成可直接打开的 HTML 变化报告。仓库自带离线 Demo，不访问真实网站也能演示。

## 程序怎样运行

读取配置 → 请求并解析网页 → 写 CSV → 把本轮商品作为完整快照写进 SQLite → 找到同一来源的上一轮 → 按商品标识比较 → 输出变化事件和 HTML 报告。

## 主要目录

- `price_scraper/`：程序主体。
- `tests/`：自动测试和离线网页样例。
- `scripts/`：可直接运行的演示脚本。
- `outputs/`：本地运行结果，不提交数据库历史。
- `docs/`：对外展示截图。

## 最重要的文件

- `price_scraper/config.py`：读取 YAML 规则。
- `price_scraper/scraper.py`：请求网页并解析商品。
- `price_scraper/storage.py`：保留原有 CSV 历史功能。
- `price_scraper/history.py`：新增 SQLite 快照和差异算法。
- `price_scraper/report.py`：新增本地 HTML 报告。
- `price_scraper/cli.py`：把旧功能与新增参数串起来。
- `tests/test_history.py`：验证快照、来源隔离、原子写入和 HTML 转义。
- `scripts/demo_history.py`：不用联网的完整演示。

## 核心代码逻辑

商品优先使用 URL 作为标识；没有 URL 时使用规范化名称。每次保存前检查一轮内是否出现重复标识。程序只取同一来源最近一轮快照，分别计算新增、消失、价格变化和库存变化。数据库写入放在一次事务中，中途失败不会留下半轮数据。

## 原有部分与新增部分

原有部分是 YAML 配置、Requests/BeautifulSoup 采集、分页、价格解析和 CSV 输出。新增部分是 SQLite 数据模型、来源隔离、稳定商品键、差异算法、HTML 报告、命令行参数、离线 Demo 和相应测试。

## 使用的技术

Python、Requests、BeautifulSoup、PyYAML、SQLite、HTML、pytest。

## 面试官可能问什么

1. 为什么不用 CSV 直接比较？SQLite 更适合按来源和运行批次查询，也能用事务保证完整性。
2. 为什么优先用 URL？商品名称可能变化或重复，但 URL 也不是绝对稳定。
3. 为什么要区分来源？不同网站的相同商品不能误认为同一条历史。
4. 为什么重复键要让整轮失败？否则无法确定哪条才代表本轮真实状态。
5. `removed` 是否等于下架？不是，页面改版或采集范围变化也可能造成未观察到。
6. 如何保护 HTML 报告？动态内容在写入前进行转义。
7. 为什么没有做动态网页？本项目范围是静态页面；Playwright 是后续扩展，不冒充已完成。

## 如何现场演示

在项目目录运行 `python scripts/demo_history.py`，打开 `outputs/demo-report.html`。指出两轮样例中的同一商品发生了价格和库存变化，再展示自动测试结果。演示数据来自静态样例，不代表真实客户或商家。
