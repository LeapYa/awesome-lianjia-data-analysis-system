<p align="center">
  <a href="#">
    <img src="frontend\src\assets\logo\logo.png" alt="Logo" width="20%">
  </a>

<h3 align="center">链家租房数据采集与分析系统</h3>
  <p align="center">
    链家租房数据采集、反爬、分析与可视化一体化平台，助力高效数据洞察与决策
    <br />
    <a href="#主要功能"><strong>探索功能特性 »</strong></a>
    <br />
    <br />
    <a href="#安装指南">快速部署</a>
    ·
    <a href="#Docker部署指南">Docker部署</a>
    ·
    <a href="#贡献指南">贡献指南</a>
    <br />
    <br />
    <a href="README_en.md">English</a> | 简体中文 | <a href="README_zh-TW.md">繁體中文</a>
  </p>
  <p align="center">
   <img src="https://img.shields.io/npm/l/gitbook-plugin-mygitalk.svg" alt="Apache License">
   <img src="https://img.shields.io/badge/language-python-%233572A5.svg" alt="Python">
   <img src="https://img.shields.io/badge/language-dockerfile-%23384D54.svg" alt="Dockerfile">
   <img src="https://img.shields.io/badge/last%20commit-today-green.svg" alt="Last Commit">
  </p>
</p>

## 项目状态

> **验证码适配已跟上，采集链路可用。**
>
> 2026-10 贝壳把点选验证码从 3 字改成 4 字、字形换成白色粗描边，旧方案失效过一段时间。现在已经重训模型并接上账号登录：三种题型（phrase / word / icon）都能识别，登录后的翻页采集已跑通。
>
> 实测：识别器单字 95%、整句 85%、语序 80.8%；带登录态采集 bj/sh/gz 各 3 页共 270 条，中途验证码自动通过。
>
> 还没做完的两件事：滑块的自动化轨迹仍会被判定为机器（偶尔触发“怪兽吃拼图”），多 IP 代理池只有表结构、没接真实代理。

## 项目概述

链家租房数据采集与分析系统曾是一个基于Python+Vue3构建的租房数据处理平台，目标是完成链家/贝壳租房数据的自动化采集、数据存储、分析和可视化展示。后端使用FastAPI构建API服务，爬虫模块结合DrissionPage实现浏览器自动化控制，数据处理采用PySpark进行批量分析，使用pgsql作为主要数据库存储爬取的房源数据和分析结果。前端基于Vue3和Element Plus构建用户界面，通过ECharts实现数据可视化。采集侧现在支持带账号登录的翻页抓取，验证码识别模型随仓库携带，见 `docs/账号池与采集.md`；数据处理、任务编排与前端展示部分保持原样。

## 数据源说明

> 
> 本项目主要爬取**贝壳找房**数据，理论上也支持**链家**数据（贝壳是链家旗下平台，两者技术架构相似，反爬机制相同）。
> 
> 系统默认爬取贝壳数据，如需切换链家只需修改配置域名，数据格式完全兼容。

## 主要功能

### 数据采集功能

- 支持单城市、多页面的自动化数据爬取
- 历史版本曾支持滑块验证码处理；当前站点验证已升级，该能力已失效
- IP代理池管理与轮换策略
- 定时任务和任务队列管理

### 数据分析功能

- 区域分析：各区域租金水平、房源数量分布
- 房型分析：不同户型的价格特征和供需情况
- 价格分布分析：租金价格分布规律和异常值检测
- 价格趋势分析：租金价格的时间变化趋势
- 多维交叉分析：房型、区域、价格等多因素关联分析

### 用户管理功能

- 用户注册、登录和身份认证
- 基于JWT的安全访问控制
- 用户设置与个人资料管理
- 账户安全保护(密码加密存储等)

### 系统管理功能

- 爬虫任务管理与监控
- 数据库配置与管理
- 系统设置与参数调整
- 代理IP管理
- 定时任务调度配置

## 技术栈

### 后端技术

- **编程语言**：Python 3.10+
- **Web框架**：FastAPI
- **数据分析**：PySpark, Pandas
- **爬虫技术**：Selenium, DrissionPage, BeautifulSoup
- **数据库**：PostgreSQL
- **认证授权**：JWT, Bcrypt
- **任务调度**：Schedule

### 前端技术

- **框架**：Vue3
- **UI组件库**：Element UI Plus
- **图表可视化**：ECharts
- **状态管理**：Vuex
- **路由管理**：Vue Router
- **HTTP客户端**：Axios

### 开发与部署工具

- **版本控制**：Git
- **开发环境**：VSCode, PyCharm
- **容器化**：Docker (可选)

## 系统架构

系统采用前后端分离的三层架构：

1. **数据采集层**：

   - 爬虫引擎：控制爬取流程和策略
   - 验证码处理器：处理各类验证码
   - IP管理器：维护和轮换代理IP
2. **数据处理与存储层**：

   - 数据处理引擎：清洗、转换和分析数据
   - 数据存储服务：管理数据库和文件存储
   - 分析引擎：执行复杂的数据分析任务
3. **应用服务层**：

   - API服务：提供RESTful接口
   - 认证服务：处理用户认证和授权
   - 前端应用：提供用户界面和交互

## 安装指南

### 环境要求

- Python 3.10+
- PostgreSQL 14+
- Node.js 14+
- Java 8+ (用于运行PySpark)

### 后端安装

1. 克隆代码仓库

```bash
git clone https://github.com/LeapYa/Awesome-lianjia-data-analysis-system.git
cd Awesome-lianjia-data-analysis-system
```

2. 安装uv
```bash
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex
# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3.创建并激活虚拟环境

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

4. 安装依赖

```bash
uv pip install -r requirements.txt
```

5. 配置数据库

```bash
# 创建PostgreSQL数据库（密码为 leapyaleapya123）
createdb -h localhost -p 5432 -U postgres rental_analysis
# 初始化数据库表结构
psql -h localhost -p 5432 -U postgres -d rental_analysis -f init.sql
```

> **💡 配置说明**
> 
> 项目已包含预配置的`.env`文件，默认数据库密码为`leapyaleapya123`。
> 如需修改，请编辑`.env`文件中的`DB_PASSWORD`字段。

### 前端安装

1. 进入前端目录

```bash
cd frontend
```

2. 安装依赖

```bash
npm install
```

3. 构建前端应用

```bash
npm run build
```

## 使用说明

### 启动服务

1. 启动后端API服务

```bash
uv run uvicorn api:app --reload
```

2. 启动前端开发服务器(开发模式)

```bash
cd frontend
npm run serve
```

3. 访问系统
   在浏览器中访问 `http://localhost:8080`

> **⚠️ 初始登录信息**
> 
> 首次部署时的登录凭据：
> - 用户名：`admin`
> - 密码：`admin123`

## Docker部署指南(推荐)

本项目提供了开箱即用的Docker Compose配置，已预配置好`.env`文件，支持在Linux系统上一键部署整个系统。

### 前置条件

- 安装Docker和Docker Compose：
  ```bash
  # 安装Docker
  curl -fsSL https://get.docker.com | sh
  
  # 安装Docker Compose
  sudo curl -L "https://github.com/docker/compose/releases/download/v2.15.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose
  ```

### 一键部署步骤

1. 克隆代码仓库
   ```bash
   git clone https://github.com/LeapYa/Awesome-lianjia-data-analysis-system.git
   cd Awesome-lianjia-data-analysis-system
   ```

2. 直接启动服务（无需额外配置）
   ```bash
   # 构建并启动所有服务
   docker-compose up -d
   
   # 查看服务状态
   docker-compose ps
   ```

3. 访问系统
   - 前端界面: http://你的IP地址
   - API服务: http://localhost:8000

> **⚠️ 初始登录信息**
> 
> 首次部署时的登录凭据：
> - 用户名：`admin`
> - 密码：`admin123`

> **💡 开箱即用配置**
> 
> 项目已预配置好`.env`文件，包含以下开发环境设置：
> - 数据库密码：`leapyaleapya123`
> - JWT密钥：开发环境专用密钥
> - 环境模式：`development`
> - 调试模式：已启用

### 服务管理

```bash
# 查看日志
docker-compose logs -f

# 重启服务
docker-compose restart

# 停止服务
docker-compose down

# 停止并删除数据卷（慎用，会删除数据库数据）
docker-compose down -v
```

### 目录挂载

- 数据库数据存储在命名卷 `postgres_data` 中
- 日志、截图、验证会话等数据挂载到本地对应目录

### 自定义配置

可以修改 `docker-compose.yml` 文件中的环境变量来自定义配置，如数据库密码、端口映射等。

### 基本操作流程

1. **用户注册/登录**：创建账户或使用现有账户登录系统。
2. **创建爬虫任务**：

   - 选择目标城市
   - 设置爬取页数
   - 提交爬取任务
3. **监控爬虫任务**：

   - 查看任务进度
   - 查看爬取结果统计
4. **数据分析**：

   - 选择分析类型
   - 设置分析参数
   - 查看分析结果和可视化图表
5. **导出数据**：

   - 导出原始数据或分析结果
   - 支持CSV、Excel等格式

## 功能模块详解

### 爬虫模块 (selenium_spider.py)

爬虫模块负责从链家网站爬取租房数据，主要功能包括：

- 使用Selenium和DrissionPage控制浏览器自动化
- 处理链家网站的验证码和反爬机制
- 提取房源信息并存储到数据库
- 管理爬虫任务和进度

### 数据处理模块 (data_processor.py)

数据处理模块负责清洗和分析爬取的数据，主要功能包括：

- 使用PySpark进行大规模数据处理
- 执行多维度数据分析
- 生成分析结果和统计信息
- 支持数据导出和报表生成

### API服务模块 (api.py)

API服务模块提供系统的RESTful接口，主要功能包括：

- 提供爬虫任务管理API
- 提供数据查询和分析API
- 提供用户管理和认证API
- 提供系统设置和配置API

### 验证码处理模块 (verification_manager.py)

验证码处理模块负责处理链家网站的各类验证码，主要功能包括：

- 识别和处理滑块验证码
- 管理验证session和cookies
- 提供验证状态监控和错误处理

### IP管理模块 (ip_manager.py)

IP管理模块负责维护和轮换代理IP，主要功能包括：

- 管理代理IP池
- 实现多种IP轮换策略
- 测试和监控代理IP状态
- 自动刷新和更新IP

### 用户认证模块 (auth_secure.py)

用户认证模块负责用户管理和访问控制，主要功能包括：

- 用户注册和登录
- JWT令牌生成和验证
- 密码加密和安全存储
- 基于角色的访问控制

### 前端应用 (frontend/)

前端应用提供用户界面和交互功能，主要页面包括：

- 首页：系统概览和快速入口
- 任务管理：创建和监控爬虫任务
- 数据分析：查看和交互式分析数据
- 用户中心：管理用户资料和设置
- 系统设置：配置系统参数和选项

## 系统特色

1. **智能验证码处理**：实现了滑块验证码的自动识别和处理，提高了爬虫的成功率。
2. **多策略IP管理**：设计了多种IP轮换策略，有效应对网站的IP封禁机制。
3. **分布式数据处理**：基于PySpark实现分布式数据处理，提高了大规模数据分析的效率。
4. **多维度数据分析**：提供了丰富的数据分析维度，满足不同用户的分析需求。
5. **完整的安全机制**：实现了用户认证、数据加密和访问控制，保障系统安全。

## 贡献指南

欢迎对本项目进行贡献！请遵循以下步骤：

1. Fork本仓库
2. 创建您的特性分支 (`git checkout -b feature/您的功能名称`)
3. 提交您的更改 (`git commit -m '添加了什么功能'`)
4. 推送到分支 (`git push origin feature/您的功能名称`)
5. 打开一个Pull Request

## 许可证

本项目采用Apache License 2.0许可证 - 详情见 [LICENSE](LICENSE) 文件。

## 联系方式

项目维护者 - [LeapYa](mailto:leapya@foxmail.com)

邮箱：leapya@foxmail.com

项目链接: [https://github.com/LeapYa/Awesome-lianjia-data-analysis-system](https://github.com/LeapYa/Awesome-lianjia-data-analysis-system)
