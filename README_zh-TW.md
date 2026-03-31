<p align="center">
  <a href="#">
    <img src="frontend\src\assets\logo\logo.png" alt="Logo" width="20%">
  </a>

<h3 align="center">鏈家租房數據採集與分析系統</h3>
  <p align="center">
    鏈家租房數據採集、反爬、分析與可視化一體化平台，助力高效數據洞察與決策
    <br />
    <a href="#主要功能"><strong>探索功能特性 »</strong></a>
    <br />
    <br />
    <a href="#安裝指南">快速部署</a>
    ·
    <a href="#Docker部署指南">Docker部署</a>
    ·
    <a href="#貢獻指南">貢獻指南</a>
    <br />
    <br />
    <a href="README_en.md">English</a> | <a href="README.md">简体中文</a> | 繁體中文
  </p>
  <p align="center">
   <img src="https://img.shields.io/npm/l/gitbook-plugin-mygitalk.svg" alt="Apache License">
   <img src="https://img.shields.io/badge/language-python-%233572A5.svg" alt="Python">
   <img src="https://img.shields.io/badge/language-dockerfile-%23384D54.svg" alt="Dockerfile">
   <img src="https://img.shields.io/badge/last%20commit-today-green.svg" alt="Last Commit">
  </p>
</p>

## 項目狀態

> **本項目已停止維護。**
>
> 由於貝殼/鏈家當前的人機驗證機制已經升級，不再是本倉庫原先適配的滑塊驗證碼方案，現有自動化採集流程已無法保證可用。
>
> 本倉庫將保留作為歷史實現與數據分析/前端架構參考，不再繼續跟進新的驗證碼適配。如果你只想復用分析與可視化部分，建議改為接入自有或合規的數據源。

## 項目概述

鏈家租房數據採集與分析系統曾是一個基於Python+Vue3構建的租房數據處理平台，目標是完成鏈家/貝殼租房數據的自動化採集、存儲、分析和可視化展示。後端使用FastAPI構建API服務，爬蟲模組結合DrissionPage實現瀏覽器自動化控制，數據處理採用PySpark進行批量分析，使用pgsql作為主要資料庫存儲爬取的房源數據和分析結果。前端基於Vue3和Element Plus構建用戶界面，通過ECharts實現數據可視化。當前倉庫更適合作為歷史實現參考，尤其是數據處理、任務編排與前端展示部分。

## 數據來源

> 
> 本項目主要爬取**貝殼找房**數據，理論上也支持**鏈家**數據（貝殼是鏈家旗下平台，兩者技術架構相似，反爬機制相同）。
> 
> 系統默認爬取貝殼數據，如需切換鏈家只需修改配置域名，數據格式完全兼容。

## 主要功能

### 數據採集功能

- 支持單城市、多頁面的自動化數據爬取
- 歷史版本曾支持滑塊驗證碼處理；當前站點驗證已升級，該能力已失效
- IP代理池管理與輪換策略
- 定時任務和任務隊列管理

### 數據分析功能

- 區域分析：各區域租金水平、房源數量分佈
- 房型分析：不同戶型的價格特徵和供需情況
- 價格分佈分析：租金價格分佈規律和異常值檢測
- 價格趨勢分析：租金價格的時間變化趨勢
- 多維交叉分析：房型、區域、價格等多因素關聯分析

### 用戶管理功能

- 用戶註冊、登錄和身份認證
- 基於JWT的安全訪問控制
- 用戶設置與個人資料管理
- 帳戶安全保護(密碼加密存儲等)

### 系統管理功能

- 爬蟲任務管理與監控
- 資料庫配置與管理
- 系統設置與參數調整
- 代理IP管理
- 定時任務調度配置

## 技術棧

### 後端技術

- **編程語言**：Python 3.10+
- **Web框架**：FastAPI
- **數據分析**：PySpark, Pandas
- **爬蟲技術**：Selenium, DrissionPage, BeautifulSoup
- **資料庫**：PostgreSQL
- **認證授權**：JWT, Bcrypt
- **任務調度**：Schedule

### 前端技術

- **框架**：Vue3
- **UI組件庫**：Element UI Plus
- **圖表可視化**：ECharts
- **狀態管理**：Vuex
- **路由管理**：Vue Router
- **HTTP客戶端**：Axios

### 開發與部署工具

- **版本控制**：Git
- **開發環境**：VSCode, PyCharm
- **容器化**：Docker (可選)

## 系統架構

系統採用前後端分離的三層架構：

1. **數據採集層**：

   - 爬蟲引擎：控制爬取流程和策略
   - 驗證碼處理器：處理各類驗證碼
   - IP管理器：維護和輪換代理IP
2. **數據處理與存儲層**：

   - 數據處理引擎：清洗、轉換和分析數據
   - 數據存儲服務：管理資料庫和文件存儲
   - 分析引擎：執行複雜的數據分析任務
3. **應用服務層**：

   - API服務：提供RESTful接口
   - 認證服務：處理用戶認證和授權
   - 前端應用：提供用戶界面和交互

## 安裝指南

### 環境要求

- Python 3.10+
- PostgreSQL 14+
- Node.js 14+
- Java 8+ (用於運行PySpark)

### 後端安裝

1. 克隆代碼倉庫

```bash
git clone https://github.com/LeapYa/Awesome-lianjia-data-analysis-system.git
cd Awesome-lianjia-data-analysis-system
```

2. 安裝uv
```bash
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex
# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3.創建並激活虛擬環境

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

4. 安裝依賴

```bash
uv pip install -r requirements.txt
```

5. 配置資料庫

```bash
# 創建PostgreSQL資料庫（密碼為 leapyaleapya123）
createdb -h localhost -p 5432 -U postgres rental_analysis
# 初始化資料庫表結構
psql -h localhost -p 5432 -U postgres -d rental_analysis -f init.sql
```

> **💡 配置說明**
> 
> 項目已包含預配置的`.env`文件，預設資料庫密碼為`leapyaleapya123`。
> 如需修改，請編輯`.env`文件中的`DB_PASSWORD`字段。

### 前端安裝

1. 進入前端目錄

```bash
cd frontend
```

2. 安裝依賴

```bash
npm install
```

3. 構建前端應用

```bash
npm run build
```

## 使用說明

### 啟動服務

1. 啟動後端API服務

```bash
uv run uvicorn api:app --reload
```

2. 啟動前端開發服務器(開發模式)

```bash
cd frontend
npm run serve
```

3. 訪問系統
   在瀏覽器中訪問 `http://localhost:8080`

> **⚠️ 初始登錄信息**
> 
> 首次部署時的登錄憑據：
> - 用戶名：`admin`
> - 密碼：`admin123`

## Docker部署指南(推薦)

本項目提供了開箱即用的Docker Compose配置，已預配置好`.env`文件，支持在Linux系統上一鍵部署整個系統。

### 前置條件

- 安裝Docker和Docker Compose：
  ```bash
  # 安裝Docker
  curl -fsSL https://get.docker.com | sh
  
  # 安裝Docker Compose
  sudo curl -L "https://github.com/docker/compose/releases/download/v2.15.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose
  ```

### 一鍵部署步驟

1. 克隆代碼倉庫
   ```bash
   git clone https://github.com/LeapYa/Awesome-lianjia-data-analysis-system.git
   cd Awesome-lianjia-data-analysis-system
   ```

2. 直接啟動服務（無需額外配置）
   ```bash
   # 構建並啟動所有服務
   docker-compose up -d
   
   # 查看服務狀態
   docker-compose ps
   ```

3. 訪問系統
   - 前端界面: http://你的IP地址:8080
   - API服務: http://localhost:8000

> **⚠️ 初始登錄信息**
> 
> 首次部署時的登錄憑據：
> - 用戶名：`admin`
> - 密碼：`admin123`

> **💡 開箱即用配置**
> 
> 項目已預配置好`.env`文件，包含以下開發環境設置：
> - 資料庫密碼：`leapyaleapya123`
> - JWT密鑰：開發環境專用密鑰
> - 環境模式：`development`
> - 調試模式：已啟用
> 
> 對於生產環境部署，請參考`.env.example`文件自行配置安全的密鑰和密碼。

### 服務管理

```bash
# 查看日誌
docker-compose logs -f

# 重啟服務
docker-compose restart

# 停止服務
docker-compose down

# 停止並刪除數據卷（慎用，會刪除資料庫數據）
docker-compose down -v
```

### 目錄掛載

- 資料庫數據存儲在命名卷 `postgres_data` 中
- 日誌、截圖、驗證會話等數據掛載到本地對應目錄

### 自定義配置

可以修改 `docker-compose.yml` 文件中的環境變量來自定義配置，如資料庫密碼、端口映射等。

### 基本操作流程

1. **用戶註冊/登錄**：創建帳戶或使用現有帳戶登錄系統。
2. **創建爬蟲任務**：

   - 選擇目標城市
   - 設置爬取頁數
   - 提交爬取任務
3. **監控爬蟲任務**：

   - 查看任務進度
   - 查看爬取結果統計
4. **數據分析**：

   - 選擇分析類型
   - 設置分析參數
   - 查看分析結果和可視化圖表
5. **導出數據**：

   - 導出原始數據或分析結果
   - 支持CSV、Excel等格式

## 功能模組詳解

### 爬蟲模組 (selenium_spider.py)

爬蟲模組負責從鏈家網站爬取租房數據，主要功能包括：

- 使用Selenium和DrissionPage控制瀏覽器自動化
- 處理鏈家網站的驗證碼和反爬機制
- 提取房源信息並存儲到資料庫
- 管理爬蟲任務和進度

### 數據處理模組 (data_processor.py)

數據處理模組負責清洗和分析爬取的數據，主要功能包括：

- 使用PySpark進行大規模數據處理
- 執行多維度數據分析
- 生成分析結果和統計信息
- 支持數據導出和報表生成

### API服務模組 (api.py)

API服務模組提供系統的RESTful接口，主要功能包括：

- 提供爬蟲任務管理API
- 提供數據查詢和分析API
- 提供用戶管理和認證API
- 提供系統設置和配置API

### 驗證碼處理模組 (verification_manager.py)

驗證碼處理模組負責處理鏈家網站的各類驗證碼，主要功能包括：

- 識別和處理滑塊驗證碼
- 管理驗證session和cookies
- 提供驗證狀態監控和錯誤處理

### IP管理模組 (ip_manager.py)

IP管理模組負責維護和輪換代理IP，主要功能包括：

- 管理代理IP池
- 實現多種IP輪換策略
- 測試和監控代理IP狀態
- 自動刷新和更新IP

### 用戶認證模組 (auth_secure.py)

用戶認證模組負責用戶管理和訪問控制，主要功能包括：

- 用戶註冊和登錄
- JWT令牌生成和驗證
- 密碼加密和安全存儲
- 基於角色的訪問控制

### 前端應用 (frontend/)

前端應用提供用戶界面和交互功能，主要頁面包括：

- 首頁：系統概覽和快速入口
- 任務管理：創建和監控爬蟲任務
- 數據分析：查看和交互式分析數據
- 用戶中心：管理用戶資料和設置
- 系統設置：配置系統參數和選項

## 系統特色

1. **智能驗證碼處理**：實現了滑塊驗證碼的自動識別和處理，提高了爬蟲的成功率。
2. **多策略IP管理**：設計了多種IP輪換策略，有效應對網站的IP封禁機制。
3. **分佈式數據處理**：基於PySpark實現分佈式數據處理，提高了大規模數據分析的效率。
4. **多維度數據分析**：提供了豐富的數據分析維度，滿足不同用戶的分析需求。
5. **完整的安全機制**：實現了用戶認證、數據加密和訪問控制，保障系統安全。

## 貢獻指南

歡迎對本項目進行貢獻！請遵循以下步驟：

1. Fork本倉庫
2. 創建您的特性分支 (`git checkout -b feature/您的功能名稱`)
3. 提交您的更改 (`git commit -m '添加了什麼功能'`)
4. 推送到分支 (`git push origin feature/您的功能名稱`)
5. 打開一個Pull Request

## 許可證

本項目採用Apache License 2.0許可證 - 詳情見 [LICENSE](LICENSE) 文件。

## 聯繫方式

項目維護者 - [LeapYa](mailto:leapya@foxmail.com)

郵箱：leapya@foxmail.com

項目連結: [https://github.com/LeapYa/Awesome-lianjia-data-analysis-system](https://github.com/LeapYa/Awesome-lianjia-data-analysis-system) 
