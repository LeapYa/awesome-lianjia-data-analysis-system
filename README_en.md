<p align="center">
  <a href="#">
    <img src="frontend\src\assets\logo\logo.png" alt="Logo" width="20%">
  </a>

<h3 align="center">Lianjia Rental Data Collection & Analysis System</h3>
  <p align="center">
    An integrated platform for Lianjia rental data collection, anti-crawling, analysis and visualization to support efficient data insights and decision-making
    <br />
    <a href="#key-features"><strong>Explore Features »</strong></a>
    <br />
    <br />
    <a href="#installation-guide">Quick Setup</a>
    ·
    <a href="#docker-deployment-guide">Docker Deployment</a>
    ·
    <a href="#contributing">Contributing</a>
    <br />
    <br />
    English | <a href="README.md">简体中文</a> | <a href="README_zh-TW.md">繁體中文</a>
  </p>
  <p align="center">
   <img src="https://img.shields.io/npm/l/gitbook-plugin-mygitalk.svg" alt="Apache License">
   <img src="https://img.shields.io/badge/language-python-%233572A5.svg" alt="Python">
   <img src="https://img.shields.io/badge/language-dockerfile-%23384D54.svg" alt="Dockerfile">
   <img src="https://img.shields.io/badge/last%20commit-today-green.svg" alt="Last Commit">
  </p>
</p>

## Project Status

> **This project is no longer maintained.**
>
> Beike/Lianjia has upgraded its human-verification flow, and it is no longer the slider CAPTCHA flow this repository was originally built around. As a result, the current automated crawling pipeline can no longer be considered reliable or usable as-is.
>
> The repository is being kept as a historical reference for the architecture, data-processing pipeline, and frontend implementation. It will not be updated to support the new verification flow. If you only want the analytics/visualization parts, use your own or other compliant data sources.

## Project Overview

The Lianjia Rental Data Collection & Analysis System was a rental data processing platform built with Python + Vue3. Its original goal was automated collection, storage, analysis, and visualization of rental data from Lianjia/Beike.

The backend uses FastAPI to build API services, the crawler module combines DrissionPage for browser automation control, data processing employs PySpark for batch analysis, and PostgreSQL serves as the primary database for storing crawled property data and analysis results.

The frontend is built with Vue3 and Element Plus for the user interface, implementing data visualization through ECharts. At this point, the repository is best treated as a reference implementation, especially for the data-processing, task-management, and frontend visualization parts.

## Data Source

> 
> This project primarily crawls rental data from **Beike** (ke.com), and theoretically also supports **Lianjia** (lianjia.com) data (Beike is a platform under Lianjia, with similar technical architecture and anti-crawling mechanisms).
> 
> The system is configured to crawl Beike data by default. To switch to Lianjia, simply modify the domain in the configuration. The data formats are fully compatible.

## Key Features

### Data Collection Features

- Support for automated data crawling from single cities across multiple pages
- Historical support for slider CAPTCHA handling; this no longer works after the upstream verification upgrade
- IP proxy pool management and rotation strategies
- Scheduled tasks and task queue management

### Data Analysis Features

- Regional Analysis: Rental price levels and property distribution by area
- Property Type Analysis: Price characteristics and supply-demand for different layouts
- Price Distribution Analysis: Rental price distribution patterns and outlier detection
- Price Trend Analysis: Time-based rental price trend changes
- Multi-dimensional Cross Analysis: Correlation analysis of property type, area, price and other factors

### User Management Features

- User registration, login, and authentication
- JWT-based secure access control
- User settings and profile management
- Account security protection (encrypted password storage, etc.)

### System Management Features

- Crawler task management and monitoring
- Database configuration and management
- System settings and parameter adjustment
- Proxy IP management
- Scheduled task configuration

## Technology Stack

### Backend Technologies

- **Programming Language**: Python 3.10+
- **Web Framework**: FastAPI
- **Data Analysis**: PySpark, Pandas
- **Web Scraping**: Selenium, DrissionPage, BeautifulSoup
- **Database**: PostgreSQL
- **Authentication**: JWT, Bcrypt
- **Task Scheduling**: Schedule

### Frontend Technologies

- **Framework**: Vue3
- **UI Component Library**: Element UI Plus
- **Chart Visualization**: ECharts
- **State Management**: Vuex
- **Routing**: Vue Router
- **HTTP Client**: Axios

### Development & Deployment Tools

- **Version Control**: Git
- **Development Environment**: VSCode, PyCharm
- **Containerization**: Docker (optional)

## System Architecture

The system adopts a three-tier architecture with frontend-backend separation:

1. **Data Collection Layer**:
   - Crawler Engine: Controls crawling processes and strategies
   - CAPTCHA Processor: Handles various types of CAPTCHAs
   - IP Manager: Maintains and rotates proxy IPs

2. **Data Processing & Storage Layer**:
   - Data Processing Engine: Cleans, transforms, and analyzes data
   - Data Storage Service: Manages database and file storage
   - Analysis Engine: Executes complex data analysis tasks

3. **Application Service Layer**:
   - API Service: Provides RESTful interfaces
   - Authentication Service: Handles user authentication and authorization
   - Frontend Application: Provides user interface and interactions

## Installation Guide

### Environment Requirements

- Python 3.10+
- PostgreSQL 14+
- Node.js 14+
- Java 8+ (for running PySpark)

### Backend Installation

1. Clone the repository

```bash
git clone https://github.com/LeapYa/Awesome-lianjia-data-analysis-system.git
cd Awesome-lianjia-data-analysis-system
```

2. Install uv
```bash
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Linux/Mac
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. Create and activate virtual environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/Mac
source venv/bin/activate
```

4. Install dependencies

```bash
uv pip install -r requirements.txt
```

5. Configure database

```bash
# Create PostgreSQL database (password: leapyaleapya123)
createdb -h localhost -p 5432 -U postgres rental_analysis
# Initialize database schema
psql -h localhost -p 5432 -U postgres -d rental_analysis -f init.sql
```

> **💡 Configuration Note**
> 
> The project includes a pre-configured `.env` file with default database password `leapyaleapya123`.
> To change the password, edit the `DB_PASSWORD` field in the `.env` file.

### Frontend Installation

1. Navigate to frontend directory

```bash
cd frontend
```

2. Install dependencies

```bash
npm install
```

3. Build frontend application

```bash
npm run build
```

## Usage Instructions

### Starting Services

1. Start backend API service

```bash
uv run uvicorn api:app --reload
```

2. Start frontend development server (development mode)

```bash
cd frontend
npm run serve
```

3. Access the system
   Open your browser and navigate to `http://localhost:8080`

> **⚠️ Initial Login Credentials**
> 
> Default login credentials for first deployment:
> - Username: `admin`
> - Password: `admin123`

## Docker Deployment Guide (Recommended)

This project provides ready-to-use Docker Compose configuration with pre-configured `.env` file, supporting one-click deployment of the entire system on Linux systems.

### Prerequisites

- Install Docker and Docker Compose:
  ```bash
  # Install Docker
  curl -fsSL https://get.docker.com | sh
  
  # Install Docker Compose
  sudo curl -L "https://github.com/docker/compose/releases/download/v2.15.1/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose
  ```

### One-Click Deployment Steps

1. Clone the repository
   ```bash
   git clone https://github.com/LeapYa/Awesome-lianjia-data-analysis-system.git
   cd Awesome-lianjia-data-analysis-system
   ```

2. Start services directly (no additional configuration required)
   ```bash
   # Build and start all services
   docker-compose up -d
   
   # Check service status
   docker-compose ps
   ```

3. Access the system
   - Frontend Interface: http://your-server-ip:8080
   - API Service: http://localhost:8000

> **⚠️ Initial Login Credentials**
> 
> Default login credentials for first deployment:
> - Username: `admin`
> - Password: `admin123`

> **💡 Ready-to-Use Configuration**
> 
> The project comes with a pre-configured `.env` file containing development environment settings:
> - Database Password: `leapyaleapya123`
> - JWT Secret: Development-specific secret key
> - Environment Mode: `development`
> - Debug Mode: Enabled
> 
> For production environments, please refer to the `.env.example` file to configure secure keys and passwords.

### Service Management

```bash
# View logs
docker-compose logs -f

# Restart services
docker-compose restart

# Stop services
docker-compose down

# Stop and remove data volumes (use with caution - deletes database data)
docker-compose down -v
```

### Directory Mounting

- Database data is stored in the named volume `postgres_data`
- Logs, screenshots, verification sessions and other data are mounted to corresponding local directories

### Custom Configuration

You can modify environment variables in the `docker-compose.yml` file to customize configurations such as database passwords, port mappings, etc.

### Basic Operation Workflow

1. **User Registration/Login**: Create an account or log in with existing credentials.
2. **Create Crawler Tasks**:
   - Select target city
   - Set number of pages to crawl
   - Submit crawling task
3. **Monitor Crawler Tasks**:
   - View task progress
   - Check crawling result statistics
4. **Data Analysis**:
   - Select analysis type
   - Set analysis parameters
   - View analysis results and visualization charts
5. **Export Data**:
   - Export raw data or analysis results
   - Support CSV, Excel and other formats

## Detailed Module Documentation

### Crawler Module (selenium_spider.py)

The crawler module is responsible for scraping rental data from Lianjia website, main features include:

- Using Selenium and DrissionPage for browser automation control
- Handling Lianjia's CAPTCHAs and anti-crawling mechanisms
- Extracting property information and storing in database
- Managing crawler tasks and progress

### Data Processing Module (data_processor.py)

The data processing module handles cleaning and analysis of crawled data, main features include:

- Using PySpark for large-scale data processing
- Executing multi-dimensional data analysis
- Generating analysis results and statistics
- Supporting data export and report generation

### API Service Module (api.py)

The API service module provides RESTful interfaces for the system, main features include:

- Providing crawler task management APIs
- Providing data query and analysis APIs
- Providing user management and authentication APIs
- Providing system settings and configuration APIs

### CAPTCHA Processing Module (verification_manager.py)

The CAPTCHA processing module handles various types of CAPTCHAs from Lianjia website, main features include:

- Recognizing and processing slider CAPTCHAs
- Managing verification sessions and cookies
- Providing verification status monitoring and error handling

### IP Management Module (ip_manager.py)

The IP management module maintains and rotates proxy IPs, main features include:

- Managing proxy IP pool
- Implementing various IP rotation strategies
- Testing and monitoring proxy IP status
- Automatic refresh and IP updates

### User Authentication Module (auth_secure.py)

The user authentication module handles user management and access control, main features include:

- User registration and login
- JWT token generation and verification
- Password encryption and secure storage
- Role-based access control

### Frontend Application (frontend/)

The frontend application provides user interface and interaction features, main pages include:

- Dashboard: System overview and quick access
- Task Management: Create and monitor crawler tasks
- Data Analysis: View and interact with data analysis
- User Center: Manage user profiles and settings
- System Settings: Configure system parameters and options

## System Highlights

1. **Intelligent CAPTCHA Processing**: Implements automatic recognition and processing of slider CAPTCHAs, improving crawler success rates.
2. **Multi-strategy IP Management**: Designed various IP rotation strategies to effectively counter website IP blocking mechanisms.
3. **Distributed Data Processing**: Uses PySpark for distributed data processing, improving efficiency of large-scale data analysis.
4. **Multi-dimensional Data Analysis**: Provides rich data analysis dimensions to meet different user analysis needs.
5. **Complete Security Mechanisms**: Implements user authentication, data encryption, and access control to ensure system security.

## Contributing

Contributions to this project are welcome! Please follow these steps:

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/YourFeatureName`)
3. Commit your changes (`git commit -m 'Add some feature'`)
4. Push to the branch (`git push origin feature/YourFeatureName`)
5. Open a Pull Request

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Contact

Project Maintainer - [LeapYa](mailto:leapya@foxmail.com)

Email: leapya@foxmail.com

Project Link: [https://github.com/LeapYa/Awesome-lianjia-data-analysis-system](https://github.com/LeapYa/Awesome-lianjia-data-analysis-system) 
