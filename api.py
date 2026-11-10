# -*- coding: utf-8 -*-
import os
import sys
import json
import logging
import datetime
import base64
import requests
from typing import List, Dict, Optional, Any
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import verification_manager
import selenium_spider
import time
import threading
from selenium import webdriver
import shutil
import schedule
import calendar
import traceback
from datetime import timedelta
import asyncio
import io
from PIL import Image
import csv
from urllib.parse import quote

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 导入爬虫和数据处理模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import selenium_spider as spider
import data_processor
import auth_secure as auth  # 使用加密版的认证模块
import ip_manager  # 导入IP管理模块
import db_config    # 导入数据库配置模块
import security_utils  # 导入安全工具模块

# 记录应用启动时间
start_time_seconds = time.time()

# 在文件顶部附近，定义全局启动时间常量
start_time = datetime.datetime.now()

# 全局变量声明
RUNNING_ANALYSIS_TASKS = 0

# 数据库连接池配置
api_connection_pool_params = {
    "maxconn": 10,  # 默认最大连接数
    "mincached": 2,  # 初始连接数  
    "maxcached": 5,  # 最大空闲连接数
    "maxshared": 3,  # 最大共享连接数
    "blocking": True,  # 连接数达到最大时阻塞等待
    "maxusage": 10000,  # 单个连接最大使用次数
    "setsession": [],  # 初始化SQL命令
    "ping": 1,  # 检查连接有效性: 0=None, 1=default, 2=when idle
    "host": None,  # 将在startup_event中设置
    "port": None,
    "user": None,
    "password": None,
    "database": None,
}

# 自定义JSON编码器，处理datetime对象
class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.datetime):
            return obj.isoformat()
        return super().default(obj)

# 确保logs目录存在
logs_dir = "logs"
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(logs_dir, "api.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("rental_api")

# 创建FastAPI应用
app = FastAPI(
    title="租房数据分析系统",
    description="基于贝壳网的租房数据爬取和分析API",
    version="1.0.0"
)

# 配置FastAPI处理JSON响应时使用自定义编码器
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8080",  # 开发环境前端
        "http://localhost:80",    # 生产环境前端
        "http://127.0.0.1:8080",  # 本地开发
        "http://127.0.0.1:80",    # 本地生产
    ],  # 只允许特定域名，提高安全性
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# 自定义JSON响应处理
@app.middleware("http")
async def custom_json_serialization(request: Request, call_next):
    response = await call_next(request)
    
    # 只处理JSONResponse
    if isinstance(response, JSONResponse):
        # 获取响应内容
        response_body = response.body
        if response_body:
            # 解码为Python对象
            content = json.loads(response_body)
            # 重新编码为JSON字符串，使用自定义编码器处理datetime
            new_content = json.dumps(content, cls=DateTimeEncoder)
            # 创建新的响应
            return Response(
                content=new_content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json"
            )
    
    return response

# 注册认证路由
app.include_router(auth.router)
import account_api
app.include_router(account_api.router)

# 数据库连接参数
DB_CONFIG = {
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "123456"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "database": os.getenv("DB_NAME", "rental_analysis")
}

# 创建API专用的数据库连接池
api_connection_pool = db_config.create_api_pool()
if not api_connection_pool:
    logger.error("无法创建API数据库连接池，程序可能无法正常工作")

def get_db_connection():
    """从API连接池获取数据库连接，并确保使用后正确归还"""
    try:
        # 添加连接等待和重试逻辑
        max_attempts = 3
        wait_time = 1  # 初始等待时间（秒）
        
        for attempt in range(max_attempts):
            try:
                conn = db_config.get_connection(api_connection_pool)
                # 设置游标工厂，以便获取字典格式的结果
                conn.cursor_factory = RealDictCursor
                logger.debug("API服务成功获取数据库连接")
                return conn
            except Exception as conn_err:
                # 如果池中没有可用连接，等待后重试
                if attempt < max_attempts - 1:
                    current_wait = wait_time * (2 ** attempt)  # 指数退避
                    logger.warning(f"获取数据库连接失败，等待 {current_wait} 秒后重试... (尝试 {attempt+1}/{max_attempts})")
                    time.sleep(current_wait)
                else:
                    # 最后一次尝试失败，抛出异常
                    raise
        
        # 如果所有尝试都失败
        raise Exception("所有获取数据库连接的尝试均失败")
        
    except Exception as e:
        logger.error(f"API服务数据库连接失败: {str(e)}")
        # 记录当前连接池状态
        try:
            if 'api_connection_pool' in globals():
                conn_info = f"连接池信息: 大小={api_connection_pool.size()}, 使用中={api_connection_pool.used}"
                logger.error(conn_info)
        except:
            pass
        raise HTTPException(status_code=500, detail="数据库连接失败")

# 添加数据库连接上下文管理器，确保自动归还连接
class DBConnectionManager:
    """数据库连接上下文管理器，确保连接在使用后被正确归还"""
    
    def __init__(self):
        self.conn = None
    
    def __enter__(self):
        self.conn = get_db_connection()
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            db_config.release_connection(api_connection_pool, self.conn)
            logger.debug("API服务成功归还数据库连接")
        return False  # 不抑制异常

# 确保在应用关闭时关闭连接池
@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行清理工作"""
    logger.info("应用关闭，清理资源...")
    
    # 关闭API连接池
    if 'api_connection_pool' in globals() and api_connection_pool:
        logger.info("正在关闭API数据库连接池...")
        try:
            # 关闭所有连接
            for i in range(api_connection_pool.maxconn):
                try:
                    conn = api_connection_pool.getconn()
                    conn.close()
                except:
                    pass
            logger.info("API数据库连接池已关闭")
        except Exception as e:
            logger.error(f"关闭API数据库连接池时出错: {str(e)}")
    
    logger.info("应用资源清理完成")

# 定义模型
class CrawlTaskCreate(BaseModel):
    city: str
    max_pages: int = 5

class CrawlTaskStatus(BaseModel):
    id: int
    city: str
    city_code: str
    start_time: datetime.datetime
    end_time: Optional[datetime.datetime] = None
    expected_end_time: Optional[datetime.datetime] = None
    status: str
    total_items: int = 0
    success_items: int = 0
    error_message: Optional[str] = None
    progress: Optional[float] = None  # 添加进度字段
    planned_pages: Optional[int] = None  # 添加计划爬取的总页数

class HouseInfo(BaseModel):
    id: int
    house_id: str
    title: str
    price: int
    location_qu: Optional[str] = None
    location_big: Optional[str] = None
    location_small: Optional[str] = None
    size: Optional[float] = None
    direction: Optional[str] = None
    room: Optional[str] = None
    floor: Optional[str] = None
    image: Optional[str] = None
    link: Optional[str] = None
    unit_price: Optional[float] = None
    room_count: Optional[int] = None
    hall_count: Optional[int] = None
    bath_count: Optional[int] = None
    crawl_time: datetime.datetime

class AnalysisRequest(BaseModel):
    city: Optional[str] = None
    task_id: Optional[int] = None
    analysis_types: List[str] = []

class AnalysisResult(BaseModel):
    id: int
    analysis_type: str
    city: Optional[str] = None
    analysis_time: datetime.datetime
    result_data: Any

# 系统设置模型
class DatabaseSettings(BaseModel):
    host: str
    port: int
    name: str
    user: str
    password: Optional[str] = None

class SystemSettings(BaseModel):
    database: Optional[DatabaseSettings] = None
    lastUpdated: datetime.datetime = datetime.datetime.now()

# 默认系统设置
DEFAULT_SYSTEM_SETTINGS = SystemSettings(
    database=DatabaseSettings(
        host=DB_CONFIG["host"],
        port=int(DB_CONFIG["port"]),
        name=DB_CONFIG["database"],
        user=DB_CONFIG["user"]
    )
)

# 系统信息
class SystemInfo(BaseModel):
    version: str = "1.0.0"
    lastUpdate: datetime.datetime = datetime.datetime.now()
    apiConnected: bool = True
    dbConnected: bool = True
    totalUsers: int = 0
    totalHouses: int = 0
    totalTasks: int = 0
    startupTime: datetime.datetime = datetime.datetime.now()
    diskUsage: Optional[str] = None

# 启动时间记录
STARTUP_TIME = datetime.datetime.now()

# 后台任务：爬取数据
def crawl_data_task(city: str, max_pages: int):
    try:
        cities = spider.get_supported_cities()
        city_code = cities.get(city)
        
        if not city_code:
            logger.error(f"不支持的城市: {city}")
            return
        
        logger.info(f"开始爬取 {city}({city_code}) 的租房信息，最大页数: {max_pages}")
        houses = spider.crawl_city(city, city_code, max_pages)
        
        logger.info(f"爬取完成，获取到 {len(houses)} 条房源数据")
    except Exception as e:
        logger.error(f"爬取任务执行失败: {str(e)}")

# 后台任务：分析数据
def analyze_data_task(city: Optional[str] = None, task_id: Optional[int] = None):
    try:
        logger.info(f"开始数据分析任务，城市: {city}, 任务ID: {task_id}")
        
        # 使用上下文管理器确保适当的连接池管理
        processor = None
        try:
            # 创建处理器时传入连接配置
            processor = data_processor.RentalDataProcessor(DB_CONFIG)
            
            # 加载数据 - 使用独立的try块来处理可能的加载错误
            df = None
            try:
                df = processor.load_data_from_db(city=city, task_id=task_id)
            except Exception as load_err:
                logger.error(f"加载数据失败: {str(load_err)}")
                raise
                
            if df is not None:
                # 执行分析
                results = processor.run_all_analysis(df, city=city)
                logger.info(f"数据分析完成，城市: {city}, 任务ID: {task_id}")
            else:
                logger.warning(f"没有找到数据进行分析，城市: {city}, 任务ID: {task_id}")
        
        except Exception as proc_err:
            logger.error(f"处理数据时出错: {str(proc_err)}")
            raise
            
        finally:
            # 确保无论如何都关闭Spark会话和连接
            if processor:
                try:
                    processor.close()
                    logger.info("数据处理器资源已释放")
                except Exception as close_err:
                    logger.error(f"关闭数据处理器失败: {str(close_err)}")
    
    except Exception as e:
        logger.error(f"分析任务执行失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
    
    finally:
        # 强制进行垃圾回收，释放资源
        import gc
        gc.collect()
        logger.info("数据分析任务资源清理完成")

# API路由

@app.get("/")
async def read_root():
    return {"message": "租房数据分析系统API", "status": "运行中"}

@app.get("/cities", response_model=Dict[str, str])
async def get_cities():
    """获取支持的城市列表"""
    return spider.get_supported_cities()

@app.post("/tasks/crawl", response_model=CrawlTaskStatus)
async def create_crawl_task(task_info: CrawlTaskCreate, background_tasks: BackgroundTasks, auth_user: dict = Depends(auth.get_current_user)):
    """创建并执行新的爬虫任务"""
    cities = spider.get_supported_cities()
    
    if task_info.city not in cities:
        raise HTTPException(status_code=400, detail="不支持的城市")
    
    city_code = cities[task_info.city]
    
    # 使用selenium_spider模块创建任务
    task_id = spider.start_crawl_task(task_info.city, city_code)
    
    if not task_id:
        raise HTTPException(status_code=500, detail="创建爬虫任务失败")
    
    # 计算预期房源数并更新任务
    expected_houses = task_info.max_pages * 30  # 每页约30个房源
    
    # # 更新任务，添加计划爬取的页数
    # with DBConnectionManager() as conn:
    #     cursor = conn.cursor()
    #     cursor.execute(
    #         "UPDATE crawl_task SET total_items = %s, planned_pages = %s WHERE id = %s",
    #         (expected_houses, task_info.max_pages, task_id)
    #     )
    #     conn.commit()
    
    logger.info(f"用户 {auth_user.get('username', 'unknown')} 创建爬虫任务成功，ID: {task_id}，城市: {task_info.city}，预期房源数: {expected_houses}，计划页数: {task_info.max_pages}")
    
    # 在后台执行爬虫任务，确保传递已创建的task_id
    background_tasks.add_task(spider.crawl_city_with_selenium, 
                             task_info.city, city_code, task_info.max_pages, task_id)
    
    # 查询并返回任务状态
    with DBConnectionManager() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM crawl_task WHERE id = %s",
            (task_id,)
        )
        task = cursor.fetchone()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return task

@app.get("/tasks", response_model=List[CrawlTaskStatus])
async def get_tasks(limit: int = 10, offset: int = 0, auth_user: dict = Depends(auth.get_current_user)):
    """获取爬虫任务列表"""
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 设置更短的查询超时 - 必须在事务外执行
            cursor.execute("SET statement_timeout TO '5000'")  # 5秒超时
            
            # 先检查表是否存在
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'crawl_task'
                );
            """)
            table_exists = cursor.fetchone()["exists"]
            
            if not table_exists:
                logger.error("任务列表查询失败：crawl_task表不存在")
                # 如果表不存在，返回空列表而不是报错
                return []
            
            # 检查planned_pages列是否存在，不存在则添加
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns 
                    WHERE table_name = 'crawl_task' AND column_name = 'planned_pages'
                );
            """)
            has_planned_pages = cursor.fetchone()["exists"]
            
            if not has_planned_pages:
                logger.info("添加planned_pages列到crawl_task表")
                cursor.execute("""
                    ALTER TABLE crawl_task 
                    ADD COLUMN planned_pages INTEGER DEFAULT NULL;
                """)
                conn.commit()
            
            # 查询任务列表和相关统计数据
            cursor.execute(
                "SELECT * FROM crawl_task ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
            tasks = cursor.fetchall()

            # 如果有任务，获取其他相关数据
            task_ids = [task['id'] for task in tasks]
            progress_map = {}
            
            if task_ids:
                try:
                    # 聚合统计每个任务的成功页数
                    cursor.execute(
                        """
                        SELECT task_id, COUNT(*) AS success_pages 
                        FROM crawled_pages 
                        WHERE success = TRUE AND task_id = ANY(%s) 
                        GROUP BY task_id
                        """,
                        (task_ids,)
                    )
                    for row in cursor.fetchall():
                        progress_map[row['task_id']] = {'success_pages': row['success_pages']}
                except Exception as e:
                    logger.warning(f"获取成功页数失败: {str(e)}")
                
                try:
                    # 聚合统计每个任务的房源数
                    cursor.execute(
                        """
                        SELECT task_id, COUNT(*) AS house_count 
                        FROM house_info 
                        WHERE task_id = ANY(%s) 
                        GROUP BY task_id
                        """,
                        (task_ids,)
                    )
                    for row in cursor.fetchall():
                        if row['task_id'] in progress_map:
                            progress_map[row['task_id']]['house_count'] = row['house_count']
                        else:
                            progress_map[row['task_id']] = {'house_count': row['house_count']}
                except Exception as e:
                    logger.warning(f"获取房源数失败: {str(e)}")
            
            # 处理每个任务的进度信息
            for task in tasks:
                pid = task['id']
                # 进度百分比
                planned_pages = task.get('planned_pages') or task.get('total_pages') or 1
                success_pages = progress_map.get(pid, {}).get('success_pages', 0)
                house_count = progress_map.get(pid, {}).get('house_count', 0)
                if task["status"] in ["完成", "失败", "Completed", "Failed"]:
                    task["progress"] = 100
                else:
                    task["progress"] = round((success_pages / planned_pages) * 100, 1) if planned_pages > 0 else 0
                task["success_pages"] = success_pages
                task["house_count"] = house_count

            logger.info(f"成功获取任务列表，返回 {len(tasks)} 条记录")
            return tasks
    except Exception as e:
        logger.error(f"获取任务列表失败详细信息: {str(e)}")
        logger.error(f"异常类型: {type(e).__name__}")
        logger.error(f"异常堆栈: {traceback.format_exc()}")
        
        # 返回空列表而不是抛出异常，减轻前端负担
        return []

@app.get("/tasks/{task_id}", response_model=CrawlTaskStatus)
async def get_task(task_id: int, auth_user: dict = Depends(auth.get_current_user)):
    """获取爬虫任务详情"""
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM crawl_task WHERE id = %s", (task_id,))
            task = cursor.fetchone()
            
            if not task:
                raise HTTPException(status_code=404, detail=f"未找到任务ID: {task_id}")
            
            # 计算任务进度
            if task["status"] in ["完成", "失败", "Completed", "Failed"]:
                task["progress"] = 100
            else:
                # 获取该任务已成功爬取的页数
                cursor.execute(
                    "SELECT COUNT(*) FROM crawled_pages WHERE task_id = %s AND success = TRUE",
                    (task_id,)
                )
                crawled_pages = cursor.fetchone()["count"]
                
                # 获取任务的计划总页数
                planned_pages = task.get("planned_pages")
                if not planned_pages:
                    # 如果没有计划页数记录，尝试估算
                    estimated_total_pages = max(1, task["total_items"] // 30)
                    planned_pages = estimated_total_pages
                
                # 计算进度
                if planned_pages > 0:
                    task["progress"] = round((crawled_pages / planned_pages) * 100, 1)
                else:
                    task["progress"] = 0
            
            return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取任务详情失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取任务详情失败: {str(e)}")

@app.get("/houses", response_model=List[HouseInfo])
async def get_houses(
    city: Optional[str] = None,
    district: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_size: Optional[float] = None,
    max_size: Optional[float] = None,
    room_count: Optional[int] = None,
    limit: int = 20,
    offset: int = 0
):
    """获取房源数据列表"""
    try:
        # 输入验证
        validated_city = None
        validated_district = None
        validated_min_price, validated_max_price = None, None
        validated_min_size, validated_max_size = None, None
        validated_room_count = None
        
        if city:
            validated_city = security_utils.validate_city_name(city)
        
        if district:
            validated_district = security_utils.validate_district_name(district)
        
        if min_price is not None or max_price is not None:
            validated_min_price, validated_max_price = security_utils.validate_price_range(min_price, max_price)
        
        if min_size is not None or max_size is not None:
            validated_min_size, validated_max_size = security_utils.validate_size_range(min_size, max_size)
        
        if room_count is not None:
            validated_room_count = security_utils.validate_room_count(room_count)
        
        validated_limit, validated_offset = security_utils.validate_pagination(limit, offset)
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        query = "SELECT h.* FROM house_info h"
        conditions = []
        params = []
        
        if validated_city:
            query += " JOIN crawl_task t ON h.task_id = t.id"
            conditions.append("t.city = %s")
            params.append(validated_city)
        
        if validated_district:
            conditions.append("h.location_qu = %s")
            params.append(validated_district)
        
        if validated_min_price is not None:
            conditions.append("h.price >= %s")
            params.append(validated_min_price)
        
        if validated_max_price is not None:
            conditions.append("h.price <= %s")
            params.append(validated_max_price)
        
        if validated_min_size is not None:
            conditions.append("h.size >= %s")
            params.append(validated_min_size)
        
        if validated_max_size is not None:
            conditions.append("h.size <= %s")
            params.append(validated_max_size)
        
        if validated_room_count is not None:
            conditions.append("h.room_count = %s")
            params.append(validated_room_count)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY h.id DESC LIMIT %s OFFSET %s"
        params.extend([validated_limit, validated_offset])
        
        cursor.execute(query, params)
        houses = cursor.fetchall()
        conn.close()
        
        return houses
    except ValueError as ve:
        logger.warning(f"输入验证失败: {str(ve)}")
        raise HTTPException(status_code=400, detail=f"输入参数错误: {str(ve)}")
    except Exception as e:
        logger.error(f"获取房源列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取房源列表失败: {str(e)}")

# 添加新的house-list路由，指向相同的处理函数
@app.get("/house-list", response_model=List[HouseInfo])
async def get_house_list(
    city: Optional[str] = None,
    district: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_size: Optional[float] = None,
    max_size: Optional[float] = None,
    room_count: Optional[int] = None,
    limit: int = 20,
    offset: int = 0
):
    """获取房源数据列表 (兼容/house-list路径)"""
    return await get_houses(
        city=city, 
        district=district, 
        min_price=min_price, 
        max_price=max_price, 
        min_size=min_size, 
        max_size=max_size, 
        room_count=room_count, 
        limit=limit, 
        offset=offset
    )

@app.get("/houses/count")
async def get_houses_count(
    city: Optional[str] = None,
    district: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_size: Optional[float] = None,
    max_size: Optional[float] = None,
    room_count: Optional[int] = None
):
    """获取符合条件的房源总数"""
    try:
        # 输入验证
        validated_city = None
        validated_district = None
        validated_min_price, validated_max_price = None, None
        validated_min_size, validated_max_size = None, None
        validated_room_count = None
        
        if city:
            validated_city = security_utils.validate_city_name(city)
        
        if district:
            validated_district = security_utils.validate_district_name(district)
        
        if min_price is not None or max_price is not None:
            validated_min_price, validated_max_price = security_utils.validate_price_range(min_price, max_price)
        
        if min_size is not None or max_size is not None:
            validated_min_size, validated_max_size = security_utils.validate_size_range(min_size, max_size)
        
        if room_count is not None:
            validated_room_count = security_utils.validate_room_count(room_count)
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        query = "SELECT COUNT(*) FROM house_info h"
        conditions = []
        params = []
        
        if validated_city:
            query += " JOIN crawl_task t ON h.task_id = t.id"
            conditions.append("t.city = %s")
            params.append(validated_city)
        
        if validated_district:
            conditions.append("h.location_qu = %s")
            params.append(validated_district)
        
        if validated_min_price is not None:
            conditions.append("h.price >= %s")
            params.append(validated_min_price)
        
        if validated_max_price is not None:
            conditions.append("h.price <= %s")
            params.append(validated_max_price)
        
        if validated_min_size is not None:
            conditions.append("h.size >= %s")
            params.append(validated_min_size)
        
        if validated_max_size is not None:
            conditions.append("h.size <= %s")
            params.append(validated_max_size)
        
        if validated_room_count is not None:
            conditions.append("h.room_count = %s")
            params.append(validated_room_count)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        cursor.execute(query, params)
        count = cursor.fetchone()["count"]
        conn.close()
        
        return {"count": count}
    except ValueError as ve:
        logger.warning(f"输入验证失败: {str(ve)}")
        raise HTTPException(status_code=400, detail=f"输入参数错误: {str(ve)}")
    except Exception as e:
        logger.error(f"获取房源数量失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取房源数量失败: {str(e)}")

@app.get("/houses/{house_id}", response_model=HouseInfo)
async def get_house(house_id: str):
    """获取房源详情"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM house_info WHERE house_id = %s", (house_id,))
        house = cursor.fetchone()
        conn.close()
        
        if not house:
            raise HTTPException(status_code=404, detail=f"未找到房源ID: {house_id}")
        
        return house
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取房源详情失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取房源详情失败: {str(e)}")

@app.post("/analysis/run", response_model=Dict[str, str])
async def run_analysis(analysis_req: AnalysisRequest, background_tasks: BackgroundTasks, auth_user: dict = Depends(auth.get_current_user)):
    """运行数据分析任务"""
    try:
        # 使用全局变量跟踪正在运行的分析任务数量
        global RUNNING_ANALYSIS_TASKS
        
        # 限制同时运行的分析任务数量
        max_concurrent_tasks = 1  # 同时只允许一个分析任务
        
        if RUNNING_ANALYSIS_TASKS >= max_concurrent_tasks:
            logger.warning(f"已有{RUNNING_ANALYSIS_TASKS}个分析任务正在运行，拒绝新的请求")
            return {"status": "rejected", "message": "系统正在进行数据分析，请稍后再试"}
        
        # 增加运行任务计数
        RUNNING_ANALYSIS_TASKS += 1
        logger.info(f"用户 {auth_user.get('username', 'unknown')} 启动分析任务，当前运行的分析任务数量: {RUNNING_ANALYSIS_TASKS}")
        
        # 定义一个包装函数，确保任务完成后减少计数
        async def run_analysis_with_cleanup(city, task_id):
            # 在内部函数顶部立即声明 global
            global RUNNING_ANALYSIS_TASKS
            
            try:
                # 执行实际分析任务
                await asyncio.to_thread(analyze_data_task, city, task_id)
            finally:
                # 无论任务成功与否，都减少计数
                RUNNING_ANALYSIS_TASKS -= 1
                logger.info(f"分析任务完成，当前运行任务数量: {RUNNING_ANALYSIS_TASKS}")
        
        # 启动后台分析任务
        background_tasks.add_task(
            run_analysis_with_cleanup, 
            analysis_req.city, 
            analysis_req.task_id
        )
        
        logger.info(f"已启动数据分析任务，城市: {analysis_req.city}, 任务ID: {analysis_req.task_id}")
        return {"status": "success", "message": "数据分析任务已启动"}
    except Exception as e:
        logger.error(f"启动数据分析任务失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        
        # 确保在发生错误时也减少任务计数
        try:
            if RUNNING_ANALYSIS_TASKS > 0:
                RUNNING_ANALYSIS_TASKS -= 1
        except:
            pass
            
        raise HTTPException(status_code=500, detail=f"启动数据分析任务失败: {str(e)}")

@app.get("/analysis/results", response_model=List[AnalysisResult])
async def get_analysis_results(
    analysis_type: Optional[str] = None,
    city: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    auth_user: dict = Depends(auth.get_current_user)
):
    """获取分析结果列表"""
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = "SELECT * FROM analysis_result"
            conditions = []
            params = []
            
            if analysis_type:
                conditions.append("analysis_type = %s")
                params.append(analysis_type)
            
            if city:
                conditions.append("city = %s")
                params.append(city)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            query += " ORDER BY analysis_time DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            results = cursor.fetchall()
            
            # 确保结果数据是正确解析的对象
            for result in results:
                if isinstance(result['result_data'], str):
                    try:
                        result['result_data'] = json.loads(result['result_data'])
                    except json.JSONDecodeError:
                        logger.warning(f"无法解析分析结果JSON: {result['id']}")
                # 如果结果已经是对象(例如psycopg2已经解析了JSONB类型)，则不需要再次解析
            
            # 不再需要手动关闭连接，DBConnectionManager会自动处理
            return results
    except Exception as e:
        logger.error(f"获取分析结果失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取分析结果失败: {str(e)}")

@app.get("/analysis/results/{result_id}", response_model=AnalysisResult)
async def get_analysis_result(result_id: int, auth_user: dict = Depends(auth.get_current_user)):
    """获取分析结果详情"""
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM analysis_result WHERE id = %s", (result_id,))
            result = cursor.fetchone()
            
            if not result:
                raise HTTPException(status_code=404, detail=f"未找到分析结果ID: {result_id}")
            
            # 解析JSON字符串为Python对象
            if isinstance(result['result_data'], str):
                try:
                    result['result_data'] = json.loads(result['result_data'])
                except json.JSONDecodeError:
                    logger.warning(f"无法解析分析结果JSON: {result_id}")
            
            # 不再需要手动关闭连接，DBConnectionManager会自动处理
            return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取分析结果详情失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取分析结果详情失败: {str(e)}")

@app.get("/analysis/types")
async def get_analysis_types():
    """获取支持的分析类型"""
    return {
        "district_analysis": "区域租金分析",
        "room_type_analysis": "户型租金分析",
        "direction_analysis": "朝向租金分析",
        "floor_analysis": "楼层租金分析",
        "price_distribution": "租金价格分布",
        "community_analysis": "小区租金分析",
        "price_stats": "区域价格统计",
        "district_heatmap": "区域热力图数据",
        "room_price_cross": "户型与价格交叉分析",
        "rental_efficiency": "租金效率分析",
        "price_trend": "租金价格趋势"
    }

@app.get("/districts", response_model=List[str])
async def get_districts(city: Optional[str] = None):
    """获取区域列表"""
    try:
        # 使用DBConnectionManager替代直接调用get_db_connection
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            query = "SELECT DISTINCT h.location_qu FROM house_info h"
            params = []
            
            if city:
                query += " JOIN crawl_task t ON h.task_id = t.id WHERE t.city = %s"
                params.append(city)
            
            query += " ORDER BY h.location_qu"
            
            cursor.execute(query, params)
            districts = [row["location_qu"] for row in cursor.fetchall() if row["location_qu"]]
            
            # 不再需要手动关闭连接，DBConnectionManager会自动处理
            return districts
    except Exception as e:
        logger.error(f"获取区域列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取区域列表失败: {str(e)}")

@app.get("/statistics/summary")
async def get_summary_statistics(city: Optional[str] = None, auth_user: dict = Depends(auth.get_current_user)):
    """获取租房市场概览统计"""
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 准备查询和参数
            query_params = []
            city_condition = ""
            
            if city:
                city_condition = "JOIN crawl_task t ON h.task_id = t.id WHERE t.city = %s"
                query_params.append(city)
            
            # 获取房源总数
            count_query = f"SELECT COUNT(*) FROM house_info h {city_condition}"
            cursor.execute(count_query, query_params)
            total_count = cursor.fetchone()["count"]
            
            # 获取平均租金
            avg_price_query = f"SELECT AVG(price) FROM house_info h {city_condition}"
            cursor.execute(avg_price_query, query_params)
            avg_price = cursor.fetchone()["avg"]
            
            # 获取平均单位面积租金
            avg_unit_price_query = f"SELECT AVG(unit_price) FROM house_info h {city_condition}"
            cursor.execute(avg_unit_price_query, query_params)
            avg_unit_price = cursor.fetchone()["avg"]
            
            # 获取租金区间分布
            price_ranges = [
                (0, 1000), (1000, 1500), (1500, 2000), (2000, 2500),
                (2500, 3000), (3000, 4000), (4000, 5000), (5000, 10000)
            ]
            
            price_distribution = []
            for min_price, max_price in price_ranges:
                range_query = f"SELECT COUNT(*) FROM house_info h {city_condition}"
                range_conditions = []
                range_params = query_params.copy()
                
                if city_condition:
                    range_conditions.append(f"t.city = %s")
                
                range_conditions.append("price >= %s")
                range_params.append(min_price)
                
                range_conditions.append("price < %s")
                range_params.append(max_price)
                
                if range_conditions:
                    if city_condition:
                        range_query += " AND " + " AND ".join(range_conditions[1:])
                    else:
                        range_query += " WHERE " + " AND ".join(range_conditions)
                
                cursor.execute(range_query, range_params)
                range_count = cursor.fetchone()["count"]
                
                price_distribution.append({
                    "range": f"{min_price}-{max_price}元",
                    "count": range_count,
                    "percentage": round(range_count / total_count * 100, 2) if total_count > 0 else 0
                })
            
            # 获取户型分布TOP5
            room_type_query = f"""
            SELECT CONCAT(room_count, '室', hall_count, '厅') as room_type, COUNT(*) as count
            FROM house_info h {city_condition}
            GROUP BY room_count, hall_count
            ORDER BY count DESC
            LIMIT 5
            """
            
            cursor.execute(room_type_query, query_params)
            room_type_distribution = [dict(row) for row in cursor.fetchall()]
            
            for item in room_type_distribution:
                item["percentage"] = round(item["count"] / total_count * 100, 2) if total_count > 0 else 0
            
            # 获取区域分布TOP5
            district_query = f"""
            SELECT location_qu as district, COUNT(*) as count
            FROM house_info h {city_condition}
            WHERE location_qu IS NOT NULL
            GROUP BY location_qu
            ORDER BY count DESC
            LIMIT 5
            """
            
            cursor.execute(district_query, query_params)
            district_distribution = [dict(row) for row in cursor.fetchall()]
            
            for item in district_distribution:
                item["percentage"] = round(item["count"] / total_count * 100, 2) if total_count > 0 else 0
            
            # 不再需要手动关闭连接，DBConnectionManager会自动处理
            return {
                "total_count": total_count,
                "avg_price": round(avg_price) if avg_price else 0,
                "avg_unit_price": round(avg_unit_price, 2) if avg_unit_price else 0,
                "price_distribution": price_distribution,
                "room_type_distribution": room_type_distribution,
                "district_distribution": district_distribution
            }
    except Exception as e:
        logger.error(f"获取租房市场概览统计失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取租房市场概览统计失败: {str(e)}")

# 在应用启动时初始化验证管理器
@app.on_event("startup")
async def startup_event():
    """应用启动时执行的初始化函数"""
    global STARTUP_TIME, api_connection_pool
    
    # 记录启动时间
    STARTUP_TIME = datetime.datetime.now()
    logger.info(f"API服务启动时间: {STARTUP_TIME}")
    
    # 初始化数据库连接池
    api_connection_pool = db_config.create_api_pool()
    if api_connection_pool:
        logger.info(f"API数据库连接池初始化成功，连接范围: {db_config.API_MIN_CONNECTIONS}-{db_config.API_MAX_CONNECTIONS}")
    else:
        logger.error("API数据库连接池初始化失败")
        
    # 读取IP代理设置
    try:
        # 使用上下文管理器更安全地管理数据库连接
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 检查ip_settings表是否存在
            cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'ip_settings')")
            if cursor.fetchone()['exists']:
                cursor.execute("SELECT * FROM ip_settings LIMIT 1")
                result = cursor.fetchone()
                if result:
                    # 如果启用了自动更换，创建自动更换IP的调度任务
                    rotation_strategy = result.get('rotation_strategy', 'manual')
                    if rotation_strategy != 'manual':
                        logger.info(f"设置IP轮换策略: {rotation_strategy}")
            else:
                logger.info("IP设置表不存在，将使用默认设置")
    except Exception as e:
        logger.error(f"读取IP设置失败: {str(e)}")
        
    # 初始化定时任务
    initialize_scheduled_tasks()
    
    # 启动调度器
    background_tasks = BackgroundTasks()
    background_tasks.add_task(start_scheduler)
    
    logger.info("API服务初始化完成")

# 在原有的爬虫任务接口中使用selenium爬虫
@app.post("/tasks/selenium_crawl", response_model=CrawlTaskStatus)
async def create_selenium_crawl_task(task_info: CrawlTaskCreate, background_tasks: BackgroundTasks, auth_user: dict = Depends(auth.get_current_user)):
    """创建并执行新的Selenium爬虫任务"""
    cities = spider.get_supported_cities()
    
    if task_info.city not in cities:
        raise HTTPException(status_code=400, detail="不支持的城市")
    
    city_code = cities[task_info.city]
    
    # 使用selenium_spider模块创建任务
    task_id = spider.start_crawl_task(task_info.city, city_code)
    
    if not task_id:
        raise HTTPException(status_code=500, detail="创建爬虫任务失败")
    
    # 计算预期房源数并更新任务
    expected_houses = task_info.max_pages * 30  # 每页约30个房源
    
    # # 更新任务，添加计划爬取的页数
    # with DBConnectionManager() as conn:
    #     cursor = conn.cursor()
    #     cursor.execute(
    #         "UPDATE crawl_task SET total_items = %s, planned_pages = %s WHERE id = %s",
    #         (expected_houses, task_info.max_pages, task_id)
    #     )
    #     conn.commit()
    
    logger.info(f"用户 {auth_user.get('username', 'unknown')} 创建Selenium爬虫任务成功，ID: {task_id}，城市: {task_info.city}，预期房源数: {expected_houses}，计划页数: {task_info.max_pages}")
    
    # 在后台执行Selenium爬虫任务，确保传递已创建的task_id
    background_tasks.add_task(spider.crawl_city_with_selenium, 
                             task_info.city, city_code, task_info.max_pages, task_id)
    
    # 查询并返回任务状态
    with DBConnectionManager() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM crawl_task WHERE id = %s",
            (task_id,)
        )
        task = cursor.fetchone()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return task

# 获取系统设置
def get_system_settings() -> SystemSettings:
    """获取系统设置"""
    with DBConnectionManager() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # 获取最新设置
            cursor.execute(
                "SELECT settings FROM system_settings ORDER BY id DESC LIMIT 1;"
            )
            result = cursor.fetchone()
            
            if result:
                # 解析JSON - 检查类型并适当处理
                settings_data = result["settings"]
                if isinstance(settings_data, str):
                    # 如果是字符串，需要解析
                    settings_dict = json.loads(settings_data)
                else:
                    # 如果已经是字典，直接使用
                    settings_dict = settings_data
                
                # 处理可能存在的旧格式设置
                if "crawler" in settings_dict:
                    del settings_dict["crawler"]
                
                # 构造完整的设置对象
                return SystemSettings(**settings_dict)
            else:
                # 返回默认设置
                logger.warning("未找到系统设置，使用默认值")
                return DEFAULT_SYSTEM_SETTINGS
        except Exception as e:
            logger.error(f"获取系统设置失败: {e}")
            return DEFAULT_SYSTEM_SETTINGS
        finally:
            cursor.close()

# 更新系统设置
def update_system_settings(settings: SystemSettings) -> bool:
    """更新系统设置"""
    with DBConnectionManager() as conn:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            # 更新设置为最新值
            settings.lastUpdated = datetime.datetime.now()
            
            # 使用自定义JSON编码器处理datetime
            settings_json = json.dumps(settings.dict(), cls=DateTimeEncoder)
            
            # 插入新设置
            cursor.execute(
                "INSERT INTO system_settings (settings) VALUES (%s);",
                (settings_json,)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"更新系统设置失败: {e}")
            conn.rollback()
            return False
        finally:
            cursor.close()

# 系统设置路由
@app.get("/settings", response_model=SystemSettings)
async def get_settings(auth_user: dict = Depends(auth.get_current_user)):
    """获取系统设置"""
    # 检查是否为管理员
    if not auth_user.get("is_admin", False):
        # 非管理员不能看到数据库设置
        settings = get_system_settings()
        settings.database = None
        return settings
    
    return get_system_settings()

@app.put("/settings", response_model=Dict[str, str])
async def update_settings(
    settings: SystemSettings, 
    auth_user: dict = Depends(auth.get_current_user)
):
    """更新系统设置"""
    # 检查是否为管理员
    if not auth_user.get("is_admin", False):
        raise HTTPException(status_code=403, detail="只有管理员可以更新系统设置")
    
    if update_system_settings(settings):
        return {"message": "系统设置已更新"}
    else:
        raise HTTPException(status_code=500, detail="更新系统设置失败")

@app.get("/settings/info", response_model=dict)
def get_system_info():
    """获取系统信息统计数据"""
    # 获取应用启动时间
    startup_time = getattr(app.state, "startup_time", start_time)
    uptime = datetime.datetime.now() - startup_time
    
    # 前端期望的字段名
    system_info = {
        "version": "1.0.0",
        "lastUpdate": datetime.datetime.now(),
        "apiStatus": "正常",
        "dbStatus": "已连接",
        "userCount": 0,         # 从totalUsers改为userCount
        "houseCount": 0,        # 从totalHouses改为houseCount
        "taskCount": 0,         # 从totalTasks改为taskCount
        "startupTime": startup_time.strftime("%Y/%m/%d %H:%M:%S"),
        "uptime": str(uptime).split('.')[0],  # 格式化为小时:分钟:秒
        "diskUsage": "未知"
    }
    
    # 尝试获取数据库连接并查询数据
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 获取用户数量
            cursor.execute("SELECT COUNT(*) FROM users")
            system_info["userCount"] = cursor.fetchone()["count"]
            
            # 获取房源数量
            cursor.execute("SELECT COUNT(*) FROM house_info")
            system_info["houseCount"] = cursor.fetchone()["count"]
            
            # 获取任务数量
            cursor.execute("SELECT COUNT(*) FROM crawl_task")
            system_info["taskCount"] = cursor.fetchone()["count"]
            
            # 获取最后更新时间
            cursor.execute("SELECT MAX(crawl_time) as last_update FROM house_info")
            last_update = cursor.fetchone()["last_update"]
            if last_update:
                system_info["lastUpdate"] = last_update
        
        # 尝试获取磁盘使用情况
        try:
            total, used, free = shutil.disk_usage("/")
            system_info["diskUsage"] = f"{used // (2**30)} GB / {total // (2**30)} GB ({used / total:.1%})"
        except Exception as e:
            logger.error(f"获取磁盘信息失败: {e}")
    
    except Exception as e:
        logger.error(f"获取系统信息失败: {e}")
        system_info["apiStatus"] = "异常"
        system_info["dbStatus"] = "连接失败"
    
    return system_info

@app.post("/settings/purge", response_model=Dict[str, str])
async def purge_data(auth_user: dict = Depends(auth.get_current_user), request: Request = None):
    """清除所有爬取的数据"""
    try:
        # 获取用户ID
        user_id = auth_user.get("id")
        if not user_id:
            logger.warning(f"未找到用户ID，用户数据: {auth_user}")
            raise HTTPException(status_code=403, detail="未找到用户ID，需要管理员权限")
            
        # 打开数据库连接
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 检查管理员权限
            try:
                cursor.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
                user_record = cursor.fetchone()
                
                logger.info(f"用户权限检查: ID={user_id}, 记录={user_record}")
                
                if not user_record:
                    logger.warning(f"未找到用户记录: ID={user_id}")
                    raise HTTPException(status_code=403, detail="用户不存在，需要管理员权限")
                
                # 修复管理员权限检查逻辑，同时接受布尔值True和字符串't'
                is_admin_value = user_record["is_admin"]
                if not is_admin_value or (isinstance(is_admin_value, str) and is_admin_value != 't'):
                    logger.warning(f"用户不是管理员: ID={user_id}, is_admin={user_record.get('is_admin')}")
                    raise HTTPException(status_code=403, detail="需要管理员权限")
            except HTTPException:
                raise
            except Exception as db_error:
                logger.error(f"检查管理员权限时出错: {str(db_error)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"检查管理员权限时出错: {str(db_error)}")
                
            logger.info(f"确认用户 {auth_user.get('username')} (ID: {user_id}) 具有管理员权限")
            
            # 获取清除前的统计数据
            house_count = 0
            task_count = 0
            try:
                cursor.execute("SELECT COUNT(*) FROM house_info")
                house_count = cursor.fetchone()["count"]
                
                cursor.execute("SELECT COUNT(*) FROM crawl_task")
                task_count = cursor.fetchone()["count"]
            except Exception as count_error:
                logger.error(f"获取统计数据时出错: {str(count_error)}", exc_info=True)
                # 继续执行，不因为统计错误中断清除操作
                
            # 执行数据清除
            try:
                # 开始事务
                cursor.execute("BEGIN")
                
                # 清除与爬虫和房源相关的数据表
                data_tables = [
                    # 先删除有外键依赖的表 - 严格按照依赖关系排序
                    "house_info",      # 依赖于 crawl_task
                    "crawled_pages",   # 依赖于 crawl_task
                    "verification_session", # 依赖于 crawl_task
                    # 再删除被依赖的表
                    "crawl_task",
                    # 其他无依赖关系的表
                    "analysis_result",
                    "crawler_lock",
                    "city_locks"
                ]
                
                sequence_tables = {
                    "house_info": "house_info_id_seq",
                    "crawl_task": "crawl_task_id_seq",
                    "crawled_pages": "crawled_pages_id_seq",
                    "analysis_result": "analysis_result_id_seq",
                    "verification_session": "verification_session_id_seq"
                }
                
                # 先删除所有数据表中的数据
                for table in data_tables:
                    if table_exists(conn, table):
                        logger.info(f"正在清除表 {table} 的数据")
                        try:
                            cursor.execute(f"DELETE FROM {table}")
                            
                            # 重置表的序列（如果有）
                            if table in sequence_tables:
                                seq_name = sequence_tables[table]
                                cursor.execute("SELECT EXISTS (SELECT FROM information_schema.sequences WHERE sequence_name = %s)", (seq_name,))
                                if cursor.fetchone()["exists"]:
                                    cursor.execute(f"ALTER SEQUENCE {seq_name} RESTART WITH 1")
                        except Exception as table_error:
                            logger.error(f"清除表 {table} 时出错: {str(table_error)}")
                            raise
                    else:
                        logger.info(f"表 {table} 不存在，跳过")
                
                # 提交事务
                cursor.execute("COMMIT")
                logger.info("所有数据清除和序列重置成功")
            except Exception as tx_error:
                # 发生错误时回滚事务
                try:
                    cursor.execute("ROLLBACK")
                except:
                    pass
                logger.error(f"清除数据事务失败，执行回滚: {str(tx_error)}", exc_info=True)
                raise HTTPException(status_code=500, detail=f"清除数据事务失败: {str(tx_error)}")
            
            # 记录日志
            logger.warning(f"管理员 {auth_user.get('username')} 已清除所有数据: {house_count} 条房源, {task_count} 个任务")
            
            # 将嵌套字典转换为字符串格式
            cleared_summary = f"{house_count}条房源, {task_count}个任务"
            
            return {
                "message": "数据清除成功",
                "cleared_items": cleared_summary
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"清除数据失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清除数据失败: {str(e)}")

@app.get("/settings/export", response_model=Dict[str, str])
async def export_data(auth_user: dict = Depends(auth.get_current_user)):
    """导出所有爬取的数据为CSV"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # 记录操作
        logger.info(f"用户 {auth_user['username']} (ID: {auth_user['id']}) 正在导出数据")
        
        # 获取所有房源数据
        cursor.execute("SELECT * FROM house_info;")
        houses = cursor.fetchall()
        
        if not houses:
            return {"message": "没有可导出的数据"}
        
        # 将房源数据转换为DataFrame
        df = pd.DataFrame(houses)
        
        # 生成导出文件名
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"rental_data_export_{timestamp}.csv"
        filepath = f"./{filename}"
        
        # 导出为CSV文件
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        
        logger.info(f"数据已导出为 {filename}")
        
        return {"message": f"数据已成功导出为 {filename}"}
    except Exception as e:
        logger.error(f"导出数据失败: {e}")
        raise HTTPException(status_code=500, detail="导出数据失败")
    finally:
        cursor.close()
        conn.close()

# 确保静态文件目录存在
static_dir = "static"
avatars_dir = os.path.join(static_dir, "avatars")

if not os.path.exists(static_dir):
    os.makedirs(static_dir)
    logger.info(f"创建静态文件目录: {static_dir}")

if not os.path.exists(avatars_dir):
    os.makedirs(avatars_dir)
    logger.info(f"创建头像目录: {avatars_dir}")

# 设置静态文件服务
app.mount("/static", StaticFiles(directory="static"), name="static")

# 检查表是否存在的辅助函数
def table_exists(conn, table_name):
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(f"""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = '{table_name}'
            );
        """)
        return cursor.fetchone()["exists"]
    except Exception as e:
        logger.error(f"检查表 {table_name} 是否存在时出错: {e}")
        return False
    finally:
        cursor.close()

# 定时任务模型
class ScheduledTaskCreate(BaseModel):
    name: str
    city: str
    pages: int = 5
    schedule: str
    time: Optional[str] = None

class ScheduledTaskUpdate(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    pages: Optional[int] = None
    schedule: Optional[str] = None
    time: Optional[str] = None
    status: Optional[str] = None

class ScheduledTask(BaseModel):
    id: int
    name: str
    city: str
    pages: int
    schedule: str
    time: Optional[str] = None
    next_run: Optional[datetime.datetime] = None
    status: str = "正常"
    created_at: datetime.datetime
    updated_at: Optional[datetime.datetime] = None

# 定时任务全局变量
scheduled_jobs = {}

# 运行定时任务的函数
def run_scheduled_task(task_id: int, city: str, pages: int):
    logger.info(f"执行定时任务 ID: {task_id}, 城市: {city}, 页数: {pages}")
    try:
        # 更新任务状态为执行中
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "UPDATE scheduled_tasks SET status = '执行中' WHERE id = %s",
            (task_id,)
        )
        conn.commit()
        
        # 获取城市代码
        city_code = spider.get_city_code(city)
        if not city_code:
            logger.error(f"无效的城市代码: {city}")
            raise ValueError(f"无效的城市代码: {city}")

        # 创建爬取任务
        crawl_task_id = spider.start_crawl_task(city, city_code)
        if not crawl_task_id:
            logger.error("创建爬取任务失败")
            raise Exception("创建爬取任务失败")
            
        # 直接使用创建的爬取任务ID执行爬取
        spider.crawl_city_with_selenium(city, city_code, pages, crawl_task_id)
        
        # 计算下次运行时间
        update_next_run_time(task_id)
        
        # 更新定时任务状态为正常
        cursor.execute(
            "UPDATE scheduled_tasks SET status = '正常' WHERE id = %s",
            (task_id,)
        )
        conn.commit()
        
    except Exception as e:
        logger.error(f"定时任务执行失败: {str(e)}")
        try:
            # 更新任务状态为错误
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "UPDATE scheduled_tasks SET status = '错误' WHERE id = %s",
                (task_id,)
            )
            conn.commit()
        except Exception as db_error:
            logger.error(f"更新任务状态失败: {str(db_error)}")
    finally:
        if conn:
            cursor.close()
            conn.close()

# 计算下次运行时间
def update_next_run_time(task_id: int):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 获取任务信息
        cursor.execute("SELECT * FROM scheduled_tasks WHERE id = %s", (task_id,))
        task = cursor.fetchone()
        
        if not task:
            return
        
        next_run = None
        now = datetime.datetime.now()
        
        schedule_type, *schedule_value = task['schedule'].split('|')
        schedule_value = schedule_value[0] if schedule_value else None
        
        if task['time']:
            time_parts = task['time'].split(':')
            hour, minute = int(time_parts[0]), int(time_parts[1])
        else:
            hour, minute = now.hour, now.minute
        
        if schedule_type == 'daily':
            # 每天同一时间运行
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)
        
        elif schedule_type == 'weekly' and schedule_value:
            # 每周特定日期运行
            weekday = int(schedule_value)
            days_ahead = weekday - now.weekday()
            if days_ahead <= 0:  # 如果今天已经是指定日期或已经过了
                days_ahead += 7
            next_run = (now + datetime.timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        elif schedule_type == 'monthly' and schedule_value:
            # 每月特定日期运行
            day = int(schedule_value)
            next_month = now.month + 1 if now.day > day or (now.day == day and now.hour > hour) else now.month
            next_year = now.year + 1 if next_month > 12 else now.year
            if next_month > 12:
                next_month = 1
            
            # 处理月末日期问题（如31日在有些月份不存在）
            _, last_day = calendar.monthrange(next_year, next_month)
            target_day = min(day, last_day)
            
            next_run = datetime.datetime(next_year, next_month, target_day, hour, minute, 0)
        
        # 更新数据库中的下次运行时间
        if next_run:
            cursor.execute(
                "UPDATE scheduled_tasks SET next_run = %s WHERE id = %s",
                (next_run, task_id)
            )
            conn.commit()
    
    except Exception as e:
        logger.error(f"计算下次运行时间失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# 初始化定时任务
def initialize_scheduled_tasks():
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            try:
                # 获取所有状态为正常的定时任务
                cursor.execute("SELECT * FROM scheduled_tasks WHERE status = '正常'")
                tasks = cursor.fetchall()
                
                # 清除之前的所有定时任务
                schedule.clear()
                
                for task in tasks:
                    schedule_task(task)
            
            except Exception as e:
                logger.error(f"初始化定时任务失败: {str(e)}")
            finally:
                cursor.close()
    except Exception as conn_error:
        logger.error(f"获取数据库连接失败: {str(conn_error)}")

# 添加定时任务到调度器
def schedule_task(task):
    task_id = task['id']
    schedule_type, *schedule_value = task['schedule'].split('|')
    schedule_value = schedule_value[0] if schedule_value else None
    
    if task['time']:
        time_parts = task['time'].split(':')
        hour, minute = int(time_parts[0]), int(time_parts[1])
    else:
        hour, minute = 0, 0  # 默认午夜
    
    job = None
    
    if schedule_type == 'daily':
        # 每天同一时间运行
        job = schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(
            run_scheduled_task, task_id=task_id, city=task['city'], pages=task['pages']
        )
    
    elif schedule_type == 'weekly' and schedule_value:
        # 每周特定日期运行
        weekday = int(schedule_value)
        weekdays = {
            0: schedule.every().sunday,
            1: schedule.every().monday,
            2: schedule.every().tuesday,
            3: schedule.every().wednesday,
            4: schedule.every().thursday,
            5: schedule.every().friday,
            6: schedule.every().saturday
        }
        
        if weekday in weekdays:
            job = weekdays[weekday].at(f"{hour:02d}:{minute:02d}").do(
                run_scheduled_task, task_id=task_id, city=task['city'], pages=task['pages']
            )
    
    elif schedule_type == 'monthly' and schedule_value:
        # 每月特定日期运行
        day = int(schedule_value)
        
        # 由于schedule库没有直接支持每月特定日期的方法
        # 我们使用schedule.every().day.at()，然后在运行前检查日期
        def monthly_job():
            current_day = datetime.datetime.now().day
            if current_day == day:
                run_scheduled_task(task_id=task_id, city=task['city'], pages=task['pages'])
        
        job = schedule.every().day.at(f"{hour:02d}:{minute:02d}").do(monthly_job)
    
    if job:
        scheduled_jobs[task_id] = job

# 定时任务调度线程
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(60)  # 每分钟检查一次

# 启动调度器线程
def start_scheduler():
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()

# 定时任务API端点
@app.post("/scheduled-tasks", response_model=ScheduledTask)
async def create_scheduled_task(task: ScheduledTaskCreate, auth_user: dict = Depends(auth.get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 验证城市代码
        city_code = spider.get_city_code(task.city)
        if not city_code:
            raise HTTPException(status_code=400, detail="无效的城市")
        
        # 计算下次运行时间
        next_run = None
        now = datetime.datetime.now()
        
        schedule_type, *schedule_value = task.schedule.split('|')
        schedule_value = schedule_value[0] if schedule_value else None
        
        if task.time:
            time_parts = task.time.split(':')
            hour, minute = int(time_parts[0]), int(time_parts[1])
        else:
            hour, minute = now.hour, now.minute
        
        if schedule_type == 'daily':
            # 每天同一时间运行
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += datetime.timedelta(days=1)
        
        elif schedule_type == 'weekly' and schedule_value:
            # 每周特定日期运行
            weekday = int(schedule_value)
            days_ahead = weekday - now.weekday()
            if days_ahead <= 0:  # 如果今天已经是指定日期或已经过了
                days_ahead += 7
            next_run = (now + datetime.timedelta(days=days_ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        elif schedule_type == 'monthly' and schedule_value:
            # 每月特定日期运行
            day = int(schedule_value)
            next_month = now.month + 1 if now.day > day or (now.day == day and now.hour > hour) else now.month
            next_year = now.year + 1 if next_month > 12 else now.year
            if next_month > 12:
                next_month = 1
            
            # 处理月末日期问题（如31日在有些月份不存在）
            _, last_day = calendar.monthrange(next_year, next_month)
            target_day = min(day, last_day)
            
            next_run = datetime.datetime(next_year, next_month, target_day, hour, minute, 0)
        
        # 插入任务记录
        cursor.execute(
            """
            INSERT INTO scheduled_tasks (name, city, pages, schedule, time, next_run, status, created_at) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *
            """,
            (task.name, task.city, task.pages, task.schedule, task.time, next_run, '正常', datetime.datetime.now())
        )
        new_task = cursor.fetchone()
        conn.commit()
        
        # 添加到调度器
        schedule_task(new_task)
        
        return new_task
    
    except Exception as e:
        conn.rollback()
        logger.error(f"创建定时任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"创建定时任务失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.get("/scheduled-tasks", response_model=List[ScheduledTask])
async def get_scheduled_tasks(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    auth_user: dict = Depends(auth.get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            "SELECT * FROM scheduled_tasks ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset)
        )
        tasks = cursor.fetchall()
        return tasks
    except Exception as e:
        logger.error(f"获取定时任务列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取定时任务列表失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.get("/scheduled-tasks/{task_id}", response_model=ScheduledTask)
async def get_scheduled_task(task_id: int, auth_user: dict = Depends(auth.get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("SELECT * FROM scheduled_tasks WHERE id = %s", (task_id,))
        task = cursor.fetchone()
        if not task:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        return task
    except Exception as e:
        logger.error(f"获取定时任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取定时任务失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.put("/scheduled-tasks/{task_id}", response_model=ScheduledTask)
async def update_scheduled_task(
    task_id: int, 
    task_update: ScheduledTaskUpdate, 
    auth_user: dict = Depends(auth.get_current_user)
):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 检查任务是否存在
        cursor.execute("SELECT * FROM scheduled_tasks WHERE id = %s", (task_id,))
        existing_task = cursor.fetchone()
        if not existing_task:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        
        # 准备更新字段
        updates = {}
        if task_update.name is not None:
            updates['name'] = task_update.name
        if task_update.city is not None:
            updates['city'] = task_update.city
        if task_update.pages is not None:
            updates['pages'] = task_update.pages
        if task_update.schedule is not None:
            updates['schedule'] = task_update.schedule
        if task_update.time is not None:
            updates['time'] = task_update.time
        if task_update.status is not None:
            updates['status'] = task_update.status
        
        if not updates:
            return existing_task
        
        # 更新任务记录
        updates['updated_at'] = datetime.datetime.now()
        
        # 构建SQL更新语句
        update_fields = ", ".join([f"{key} = %s" for key in updates.keys()])
        update_values = list(updates.values())
        
        cursor.execute(
            f"UPDATE scheduled_tasks SET {update_fields} WHERE id = %s RETURNING *",
            update_values + [task_id]
        )
        updated_task = cursor.fetchone()
        conn.commit()
        
        # 如果状态改为暂停，从调度器中移除任务
        if task_update.status == '暂停' and task_id in scheduled_jobs:
            schedule.cancel_job(scheduled_jobs[task_id])
            del scheduled_jobs[task_id]
        
        # 如果状态改为正常，将任务添加到调度器
        elif (task_update.status == '正常' or 
              (task_update.status is None and existing_task['status'] == '正常')) and (
              task_update.schedule is not None or 
              task_update.time is not None or 
              task_update.city is not None or 
              task_update.pages is not None):
            
            # 从调度器中移除旧任务
            if task_id in scheduled_jobs:
                schedule.cancel_job(scheduled_jobs[task_id])
                del scheduled_jobs[task_id]
            
            # 添加新任务到调度器
            schedule_task(updated_task)
        
        # 更新下次运行时间
        update_next_run_time(task_id)
        
        # 重新获取更新后的任务（包含更新的next_run字段）
        cursor.execute("SELECT * FROM scheduled_tasks WHERE id = %s", (task_id,))
        updated_task = cursor.fetchone()
        
        return updated_task
    
    except Exception as e:
        conn.rollback()
        logger.error(f"更新定时任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新定时任务失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()

@app.delete("/scheduled-tasks/{task_id}", response_model=dict)
async def delete_scheduled_task(task_id: int, auth_user: dict = Depends(auth.get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 检查任务是否存在
        cursor.execute("SELECT * FROM scheduled_tasks WHERE id = %s", (task_id,))
        existing_task = cursor.fetchone()
        if not existing_task:
            raise HTTPException(status_code=404, detail="定时任务不存在")
        
        # 从调度器中删除任务
        if task_id in scheduled_jobs:
            schedule.cancel_job(scheduled_jobs[task_id])
            del scheduled_jobs[task_id]
        
        # 从数据库中删除任务
        cursor.execute("DELETE FROM scheduled_tasks WHERE id = %s", (task_id,))
        conn.commit()
        
        return {"message": "定时任务已删除", "task_id": task_id}
    
    except Exception as e:
        conn.rollback()
        logger.error(f"删除定时任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"删除定时任务失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# IP管理模型
class ProxyCreate(BaseModel):
    ip: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None

class Proxy(BaseModel):
    id: int
    ip: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    location: Optional[str] = None
    status: str
    latency: Optional[int] = None
    last_used: Optional[datetime.datetime] = None
    last_checked: Optional[datetime.datetime] = None

class IpSettings(BaseModel):
    rotation_strategy: str  # manual, time, failure, request
    rotation_interval: int  # 分钟
    max_failures: int
    auto_add_proxies: bool

class CurrentIp(BaseModel):
    ip: str
    location: str
    isp: Optional[str] = None
    last_changed: Optional[datetime.datetime] = None

# IP管理接口
@app.get("/api/ip/current", response_model=CurrentIp)
async def get_current_ip(auth_user: dict = Depends(auth.get_current_user)):
    """获取当前IP信息"""
    try:
        ip_info = ip_manager.get_current_ip()
        return CurrentIp(
            ip=ip_info.ip,
            location=ip_info.location,
            isp=ip_info.isp,
            last_changed=ip_info.last_changed
        )
    except Exception as e:
        logger.error(f"获取当前IP信息失败: {str(e)}")
        raise HTTPException(status_code=500, detail="获取当前IP信息失败")

@app.post("/api/ip/refresh", response_model=Dict[str, Any])
async def refresh_ip(auth_user: dict = Depends(auth.get_current_user)):
    """刷新当前IP"""
    try:
        success = ip_manager.refresh_ip()
        if success:
            return {"success": True, "message": "IP已成功刷新"}
        else:
            return {"success": False, "message": "IP刷新失败，可能没有可用的代理"}
    except Exception as e:
        logger.error(f"刷新IP失败: {str(e)}")
        raise HTTPException(status_code=500, detail="刷新IP失败")

@app.get("/api/ip/proxies", response_model=List[Proxy])
async def get_proxies(auth_user: dict = Depends(auth.get_current_user)):
    """获取所有代理"""
    try:
        proxies = ip_manager.get_proxy_list()
        return [
            Proxy(
                id=proxy.id,
                ip=proxy.ip,
                port=proxy.port,
                username=proxy.username,
                password=proxy.password,
                location=proxy.location,
                status=proxy.status,
                latency=proxy.latency,
                last_used=proxy.last_used,
                last_checked=proxy.last_checked
            )
            for proxy in proxies
        ]
    except Exception as e:
        logger.error(f"获取代理列表失败: {str(e)}")
        raise HTTPException(status_code=500, detail="获取代理列表失败")

@app.post("/api/ip/proxies", response_model=Dict[str, Any])
async def add_proxy(proxy: ProxyCreate, auth_user: dict = Depends(auth.get_current_user)):
    """添加新代理"""
    try:
        # 创建新代理对象
        new_proxy = ip_manager.ProxyInfo(
            ip=proxy.ip,
            port=proxy.port,
            username=proxy.username,
            password=proxy.password
        )
        
        # 尝试获取位置信息
        location = ip_manager.get_location(proxy.ip)
        if location:
            new_proxy.location = location
        
        # 添加代理
        success = ip_manager.add_proxy(new_proxy)
        if success:
            return {"success": True, "message": "代理添加成功"}
        else:
            return {"success": False, "message": "代理添加失败，可能已存在"}
    except Exception as e:
        logger.error(f"添加代理失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"添加代理失败: {str(e)}")

@app.delete("/api/ip/proxies/{proxy_id}", response_model=Dict[str, Any])
async def delete_proxy(proxy_id: int, auth_user: dict = Depends(auth.get_current_user)):
    """删除代理"""
    try:
        success = ip_manager.delete_proxy(proxy_id)
        if success:
            return {"success": True, "message": "代理删除成功"}
        else:
            return {"success": False, "message": "代理删除失败，可能不存在"}
    except Exception as e:
        logger.error(f"删除代理失败: {str(e)}")
        raise HTTPException(status_code=500, detail="删除代理失败")

@app.post("/api/ip/proxies/{proxy_id}/test", response_model=Dict[str, Any])
async def test_proxy(proxy_id: int, auth_user: dict = Depends(auth.get_current_user)):
    """测试代理"""
    try:
        success = ip_manager.test_proxy(proxy_id)
        if success:
            return {"success": True, "message": "代理测试成功"}
        else:
            return {"success": False, "message": "代理测试失败"}
    except Exception as e:
        logger.error(f"测试代理失败: {str(e)}")
        raise HTTPException(status_code=500, detail="测试代理失败")

@app.get("/api/ip/settings", response_model=IpSettings)
async def get_ip_settings(auth_user: dict = Depends(auth.get_current_user)):
    """获取IP设置"""
    try:
        settings = ip_manager.get_ip_settings()
        return IpSettings(
            rotation_strategy=settings.rotation_strategy,
            rotation_interval=settings.rotation_interval,
            max_failures=settings.max_failures,
            auto_add_proxies=settings.auto_add_proxies
        )
    except Exception as e:
        logger.error(f"获取IP设置失败: {str(e)}")
        raise HTTPException(status_code=500, detail="获取IP设置失败")

@app.post("/api/ip/settings", response_model=Dict[str, Any])
async def save_ip_settings(settings: IpSettings, auth_user: dict = Depends(auth.get_current_user)):
    """保存IP设置"""
    try:
        # 创建设置对象
        ip_settings = ip_manager.IpSettings(
            rotation_strategy=settings.rotation_strategy,
            rotation_interval=settings.rotation_interval,
            max_failures=settings.max_failures,
            auto_add_proxies=settings.auto_add_proxies
        )
        
        success = ip_manager.save_ip_settings(ip_settings)
        if success:
            return {"success": True, "message": "IP设置保存成功"}
        else:
            return {"success": False, "message": "IP设置保存失败"}
    except Exception as e:
        logger.error(f"保存IP设置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"保存IP设置失败: {str(e)}")

@app.get("/dashboard")
async def get_dashboard_stats(auth_user: dict = Depends(auth.get_current_user)):
    """获取仪表盘统计数据"""
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 获取房源总数
            cursor.execute("SELECT COUNT(*) FROM house_info")
            total_houses = cursor.fetchone()["count"]
            
            # 计算增长率
            growth_rate = 0
            
            # 获取7天前的记录数
            seven_days_ago = datetime.datetime.now() - datetime.timedelta(days=7)
            cursor.execute(
                "SELECT COUNT(*) FROM house_info WHERE crawl_time < %s",
                (seven_days_ago,)
            )
            old_count = cursor.fetchone()["count"]
            
            if old_count > 0:
                growth_rate = round(((total_houses - old_count) / old_count) * 100, 1)
            
            # 计算覆盖的城市数量
            cursor.execute("SELECT COUNT(DISTINCT city) FROM crawl_task")
            covered_cities = cursor.fetchone()["count"]
            
            # 计算任务总数
            cursor.execute("SELECT COUNT(*) FROM crawl_task")
            task_count = cursor.fetchone()["count"]
            
            # 获取5个最近的任务
            cursor.execute("""
                SELECT id, city, city_code, start_time, end_time, status, 
                       total_items, success_items as crawl_count
                FROM crawl_task 
                ORDER BY start_time DESC 
                LIMIT 5
            """)
            recent_tasks = []
            for row in cursor.fetchall():
                recent_tasks.append({
                    "id": row["id"],
                    "city": row["city"],
                    "city_code": row["city_code"],
                    "start_time": row["start_time"],
                    "end_time": row["end_time"],
                    "status": row["status"],
                    "total_items": row["total_items"],
                    "success_items": row["crawl_count"]
                })
            
            return {
                "house_count": total_houses,
                "growth_rate": growth_rate,
                "city_count": covered_cities,
                "task_count": task_count,
                "recent_tasks": recent_tasks
            }
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {str(e)}")
        # 发生错误时返回默认值
        return {
            "house_count": 0,
            "growth_rate": 0,
            "city_count": 0, 
            "task_count": 0,
            "recent_tasks": []
        }

@app.delete("/tasks/{task_id}", response_model=Dict[str, Any])
async def delete_task(task_id: int, auth_user: dict = Depends(auth.get_current_user)):
    """删除爬虫任务记录"""
    conn = None
    try:
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 首先检查任务是否存在
            cursor.execute("SELECT id, status FROM crawl_task WHERE id = %s", (task_id,))
            task = cursor.fetchone()
            
            if not task:
                raise HTTPException(status_code=404, detail="任务不存在")
            
            # 检查任务是否可以删除（只能删除已完成或失败的任务）
            if task["status"] not in ["完成", "失败", "Completed", "Failed"]:
                raise HTTPException(status_code=400, detail="只能删除已完成或失败的任务")
            
            # 删除任务相关的页面记录
            cursor.execute("DELETE FROM crawled_pages WHERE task_id = %s", (task_id,))
            
            # 删除与该任务相关的房源信息
            cursor.execute("DELETE FROM house_info WHERE task_id = %s", (task_id,))
            
            # 最后删除任务本身
            cursor.execute("DELETE FROM crawl_task WHERE id = %s", (task_id,))
            
            conn.commit()
            
            return {"message": "任务删除成功", "task_id": task_id}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除任务失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"删除任务失败: {str(e)}")

# 图片代理相关处理

# 图片缓存，避免重复请求相同图片
image_cache = {}
# 缓存过期时间（秒）
CACHE_EXPIRY = 3600  # 1小时

@app.get("/proxy/image")
async def proxy_image(url: str):
    """
    代理获取图片并返回base64编码
    
    Args:
        url: 原始图片URL
    
    Returns:
        包含base64编码图片的JSON响应
    """
    try:
        # 如果缓存中有且未过期，直接返回
        current_time = time.time()
        if url in image_cache and current_time - image_cache[url]["timestamp"] < CACHE_EXPIRY:
            return {"base64": image_cache[url]["data"]}
        
        # 设置超时和请求头
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        # 发送请求获取图片
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # 如果请求不成功则抛出异常
        
        # 图片处理和转换为base64
        try:
            # 使用PIL处理图片并可选地调整大小
            image = Image.open(io.BytesIO(response.content))
            
            # 调整为合理的大小，如果图片太大
            max_size = (800, 600)  # 设置最大宽高
            if image.width > max_size[0] or image.height > max_size[1]:
                image.thumbnail(max_size, Image.LANCZOS)
            
            # 如果图片是P模式(调色板模式)，先转换为RGB模式再保存为JPEG
            if image.mode == 'P':
                image = image.convert('RGB')
            
            # 转换为JPEG格式，减小大小
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=85)
            output.seek(0)
            
            # 转为base64
            base64_data = base64.b64encode(output.read()).decode('utf-8')
            base64_image = f"data:image/jpeg;base64,{base64_data}"
            
            # 存入缓存
            image_cache[url] = {
                "data": base64_image,
                "timestamp": current_time
            }
            
            # 如果缓存过大，清理最旧的条目
            if len(image_cache) > 500:  # 设置最大缓存条目数
                # 按时间戳排序，删除最旧的20%
                sorted_items = sorted(image_cache.items(), key=lambda x: x[1]["timestamp"])
                items_to_remove = sorted_items[:int(len(sorted_items) * 0.2)]
                for key, _ in items_to_remove:
                    del image_cache[key]
            
            return {"base64": base64_image}
        
        except Exception as img_err:
            logger.error(f"图片处理失败: {str(img_err)}")
            raise HTTPException(status_code=500, detail=f"图片处理失败: {str(img_err)}")
            
    except requests.RequestException as err:
        logger.error(f"获取图片失败: {str(err)}")
        raise HTTPException(status_code=500, detail=f"获取图片失败: {str(err)}")
    except Exception as e:
        logger.error(f"图片代理服务错误: {str(e)}")
        raise HTTPException(status_code=500, detail=f"图片代理服务错误: {str(e)}")

@app.get("/export/houses")
async def export_houses(
    city: Optional[str] = None,
    district: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    min_size: Optional[float] = None,
    max_size: Optional[float] = None,
    room_count: Optional[int] = None,
    task_id: Optional[int] = None,
    auth_user: dict = Depends(auth.get_current_user)
):
    """导出房源数据为CSV文件"""
    import io
    import csv
    from urllib.parse import quote
    
    try:
        # 使用上下文管理器获取数据库连接
        with DBConnectionManager() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # 构建查询
            query = "SELECT h.* FROM house_info h"
            conditions = []
            params = []
            
            if city:
                query += " JOIN crawl_task t ON h.task_id = t.id"
                conditions.append("t.city = %s")
                params.append(city)
            
            if task_id:
                conditions.append("h.task_id = %s")
                params.append(task_id)
                
            if district:
                conditions.append("h.location_qu = %s")
                params.append(district)
            
            if min_price is not None:
                conditions.append("h.price >= %s")
                params.append(min_price)
            
            if max_price is not None:
                conditions.append("h.price <= %s")
                params.append(max_price)
            
            if min_size is not None:
                conditions.append("h.size >= %s")
                params.append(min_size)
            
            if max_size is not None:
                conditions.append("h.size <= %s")
                params.append(max_size)
            
            if room_count is not None:
                conditions.append("h.room_count = %s")
                params.append(room_count)
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            
            # 限制最大导出数量，避免内存问题
            query += " ORDER BY h.id DESC LIMIT 10000"
            
            cursor.execute(query, params)
            houses = cursor.fetchall()
            
            if not houses:
                logger.warning("没有符合条件的房源数据可导出")
                return JSONResponse(status_code=404, content={"message": "没有符合条件的房源数据可导出"})
            
            # 创建CSV文件
            output = io.StringIO()
            output.write('\ufeff')  # 添加BOM，确保Excel能正确显示中文
            
            # 创建CSV写入器
            writer = csv.writer(output)
            
            # 写入表头
            headers = [
                '标题', '价格(元/月)', '区域', '小区', '小区位置', '面积(㎡)', 
                '户型', '朝向', '楼层', '爬取时间', '单价', '图片URL', '链接'
            ]
            writer.writerow(headers)
            
            # 写入数据行
            for house in houses:
                # 安全处理可能为None的字段
                row_data = [
                    str(house['title'] or ''),
                    str(house['price'] or ''),
                    str(house['location_qu'] or ''),
                    str(house['location_big'] or ''),
                    str(house['location_small'] or ''),
                    str(house['size'] or ''),
                    str(house['room'] or ''),
                    str(house['direction'] or ''),
                    str(house['floor'] or ''),
                    house['crawl_time'].strftime('%Y-%m-%d %H:%M:%S') if house['crawl_time'] else '',
                    str(house['unit_price'] or ''),
                    str(house['image'] or ''),
                    str(house['link'] or house.get('url', ''))
                ]
                writer.writerow(row_data)
            
            # 创建响应
            output.seek(0)
            timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            filter_info = ""
            if city:
                filter_info += f"_{city}"
            if district:
                filter_info += f"_{district}"
                
            # 创建ASCII兼容的文件名
            filename = f"HouseData{filter_info}_{timestamp}.csv"
            encoded_filename = quote(filename)
            
            # 记录导出操作
            logger.info(f"用户导出了 {len(houses)} 条房源数据")
            
            # 返回流式响应
            response = StreamingResponse(
                iter([output.getvalue()]),
                media_type="text/csv; charset=utf-8"
            )
            
            # 设置文件名，兼容中文
            response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"
            return response
            
    except Exception as e:
        logger.error(f"导出房源数据失败: {str(e)}")
        logger.error(f"错误详情: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"导出房源数据失败: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 