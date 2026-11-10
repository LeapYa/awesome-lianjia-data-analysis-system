<template>
  <div class="app">
    <el-container>
      <!-- 侧边栏 -->
      <el-aside :width="isCollapse ? '64px' : '240px'">
        <div class="sidebar">
          <div class="logo">
            <router-link to="/">
              <div class="logo-wrapper">
                <div class="logo-icon">
                  <img src="@/assets/logo/logo.png" alt="Logo" class="logo-image" />
                </div>
                <h1 v-if="!isCollapse">{{ $t('app.title') }}</h1>
              </div>
            </router-link>
            <div class="sidebar-toggle" @click="isCollapse = !isCollapse">
              <el-icon><Fold v-if="!isCollapse" /><Expand v-else /></el-icon>
            </div>
          </div>
          <el-menu
            :default-active="activeIndex"
            class="sidebar-menu"
            :collapse="isCollapse"
            :collapse-transition="false"
            router
          >
            <el-menu-item index="/">
              <el-icon><HomeFilled /></el-icon>
              <span>{{ $t('menu.home') }}</span>
            </el-menu-item>
            <el-menu-item index="/task-list">
              <el-icon><List /></el-icon>
              <span>{{ $t('menu.tasks') }}</span>
            </el-menu-item>
            <el-menu-item index="/account-pool">
              <el-icon><UserFilled /></el-icon>
              <span>{{ $t('menu.accountPool') }}</span>
            </el-menu-item>
          <el-menu-item index="/ip-management">
              <el-icon><Connection /></el-icon>
              <span>{{ $t('menu.ipManagement') }}</span>
            </el-menu-item>
            <el-menu-item index="/scheduled-tasks">
              <el-icon><Timer /></el-icon>
              <span>{{ $t('menu.scheduledTasks') }}</span>
            </el-menu-item>
            <el-menu-item index="/house-list">
              <el-icon><Document /></el-icon>
              <span>{{ $t('menu.houses') }}</span>
            </el-menu-item>
            <el-menu-item index="/analysis">
              <el-icon><DataAnalysis /></el-icon>
              <span>{{ $t('menu.analysis') }}</span>
            </el-menu-item>
          </el-menu>
          <div class="sidebar-footer">
            <router-link to="/settings" class="footer-item">
              <el-icon><Setting /></el-icon>
              <span>{{ $t('menu.settings') }}</span>
            </router-link>
          </div>
        </div>
      </el-aside>
      
      <el-container>
        <el-header height="70px">
          <div class="header-content">
            <div class="left">
              <el-icon class="toggle-sidebar" @click="isCollapse = !isCollapse">
                <component :is="isCollapse ? 'Expand' : 'Fold'"></component>
              </el-icon>
              <div class="breadcrumb">
                <h2>{{ getRouteTitle($route) }}</h2>
              </div>
            </div>
            <div class="right">
              <language-switcher />
              
              <!-- 未登录状态显示登录/注册按钮 -->
              <div v-if="!isLoggedIn" class="auth-buttons">
                <router-link to="/login">
                  <el-button type="primary" size="small">{{ $t('auth.login') }}</el-button>
                </router-link>
                <router-link to="/register">
                  <el-button size="small">{{ $t('auth.register') }}</el-button>
                </router-link>
              </div>
              
              <!-- 已登录状态显示用户头像和下拉菜单 -->
              <el-dropdown v-else @command="handleCommand">
                <div class="user-profile">
                  <div class="avatar">
                    <el-avatar :size="32" :src="userAvatar">{{ userInitials }}</el-avatar>
                  </div>
                  <span class="username">{{ username }}</span>
                  <el-icon><arrow-down /></el-icon>
                </div>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="profile">
                      <el-icon><user /></el-icon>
                      {{ $t('user.profile') }}
                    </el-dropdown-item>
                    <el-dropdown-item command="settings">
                      <el-icon><setting /></el-icon>
                      {{ $t('user.settings') }}
                    </el-dropdown-item>
                    <el-dropdown-item divided command="logout">
                      <el-icon><switch-button /></el-icon>
                      {{ $t('user.logout') }}
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </div>
        </el-header>
        
        <el-main>
          <!-- 路由视图 -->
          <router-view v-slot="{ Component }">
            <transition name="fade" mode="out-in">
              <component :is="Component" />
            </transition>
          </router-view>
        </el-main>
        
        <el-footer>
          <div class="footer">
            <p>{{ $t('app.footer', { year: currentYear }) }}</p>
          </div>
        </el-footer>
      </el-container>
    </el-container>
  </div>
</template>

<script>
import { computed, ref, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { Home, List, Document, DataAnalysis, HomeFilled, Fold, Setting, User, UserFilled, SwitchButton, ArrowDown, Timer, Connection, Expand } from '@element-plus/icons-vue';
import LanguageSwitcher from './components/LanguageSwitcher.vue';
import api from './api/api';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useI18n } from 'vue-i18n';

export default {
  name: 'App',
  
  components: {
    Home,
    UserFilled,
    List,
    Document,
    DataAnalysis,
    HomeFilled,
    Fold,
    Setting,
    LanguageSwitcher,
    User,
    SwitchButton,
    ArrowDown,
    Timer,
    Connection,
    Expand
  },
  
  setup() {
    const route = useRoute();
    const router = useRouter();
    const isCollapse = ref(false);
    const { t } = useI18n();

    // 用户认证状态
    const isLoggedIn = ref(false);
    const username = ref('');
    const userAvatar = ref('');

    // 获取用户首字母作为头像备选显示
    const userInitials = computed(() => {
      if (!username.value) return '';
      return username.value.charAt(0).toUpperCase();
    });

    // 获取路由标题
    const getRouteTitle = (route) => {
      if (!route.name) return '';
      
      // 特殊处理登录和注册页面
      if (route.name === 'Login') {
        return t('auth.login');
      } else if (route.name === 'Register') {
        return t('auth.register');
      } else if (route.name === 'ForgotPassword') {
        return t('auth.forgotPassword');
      } else if (route.name === 'ResetPassword') {
        return t('auth.resetPasswordTitle');
      }
      
      // 其他路由按名称匹配
      const routeMap = {
        'Home': 'nav.home',
        'TaskList': 'nav.taskList', 
        'HouseList': 'nav.houseList',
        'Analysis': 'nav.analysis',
        'IpManagement': 'nav.ipManagement',
        'ScheduledTasks': 'nav.scheduledTasks'
      };
      
      // 使用映射表获取正确的翻译键
      const key = routeMap[route.name] || `nav.${route.name}`;
      return t(key);
    };

    // 检查用户是否已登录
    const checkAuthStatus = () => {
      const token = localStorage.getItem('token');
      isLoggedIn.value = !!token;
      
      if (isLoggedIn.value) {
        // 获取用户信息
        loadUserProfile();
      }
    };

    // 加载用户个人资料
    const loadUserProfile = async () => {
      try {
        const userData = await api.getUserProfile();
        username.value = userData.username || userData.name || '用户';
        userAvatar.value = userData.avatar || '';
      } catch (error) {
        console.error('获取用户信息失败:', error);
        // 如果获取用户信息失败，可能是令牌无效
        if (error.status === 401) {
          logout();
        }
      }
    };

    // 下拉菜单命令处理
    const handleCommand = (command) => {
      switch (command) {
        case 'profile':
          router.push('/user/profile');
          break;
        case 'settings':
          router.push('/user/settings');
          break;
        case 'logout':
          confirmLogout();
          break;
      }
    };

    // 确认登出
    const confirmLogout = () => {
      ElMessageBox.confirm(
        '确定要退出登录吗?',
        '退出确认',
        {
          confirmButtonText: '确认退出',
          cancelButtonText: '取消',
          type: 'warning'
        }
      ).then(() => {
        logout();
      }).catch(() => {
        // 用户取消退出
      });
    };

    // 执行登出操作
    const logout = async () => {
      try {
        // 调用登出API
        await api.logout();
      } catch (error) {
        console.error('登出失败:', error);
      } finally {
        // 无论API是否成功，都清除本地存储并重定向
        localStorage.removeItem('token');
        isLoggedIn.value = false;
        username.value = '';
        userAvatar.value = '';
        
        ElMessage.success('已成功退出登录');
        router.push('/login');
      }
    };

    // 组件挂载时检查认证状态
    onMounted(() => {
      checkAuthStatus();
    });

    // 当前活动的菜单项
    const activeIndex = computed(() => {
      return route.path;
    });
    
    // 当前年份
    const currentYear = new Date().getFullYear();
    
    return {
      activeIndex,
      currentYear,
      isCollapse,
      isLoggedIn,
      username,
      userAvatar,
      userInitials,
      handleCommand,
      confirmLogout,
      logout,
      getRouteTitle
    };
  }
};
</script>

<style>
/* 全局样式 */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
  background-color: #f9fafb;
  color: #111827;
  font-size: 14px;
}

/* 应用整体样式 */
.app {
  height: 100vh;
  overflow: hidden;
  background-color: #f9fafb;
}

/* Element Container组件样式调整 */
.el-container {
  height: 100%;
}

.el-aside {
  transition: width 0.3s;
  background-color: #ffffff;
  box-shadow: 0 0 15px rgba(0, 0, 0, 0.05);
  position: relative;
  z-index: 10;
}

.el-header {
  background-color: #ffffff;
  border-bottom: 1px solid #f0f0f0;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.03);
  padding: 0 20px;
}

.el-main {
  padding: 20px;
  overflow-y: auto;
  background-color: #f9fafb;
}

.el-footer {
  padding: 0;
  height: 50px !important;
  background-color: #ffffff;
  border-top: 1px solid #f0f0f0;
}

/* 侧边栏样式 */
.sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
  background-color: #ffffff;
  border-right: 1px solid #efefef;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
  transition: all 0.3s ease;
}

.logo {
  display: flex;
  align-items: center;
  height: 70px;
  padding: 0 12px;
  background: linear-gradient(to right, #f0f7ff, #ffffff);
  border-bottom: 1px solid #f0f7ff;
  position: relative;
}

.logo-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
  transition: all 0.3s ease;
}

.logo-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  background: linear-gradient(135deg, #0051c3, #2d82fe);
  margin: 0;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 81, 195, 0.2);
}

.logo-image {
  width: 80%;
  height: 80%;
  object-fit: contain;
  filter: brightness(0) invert(1);
}

.logo a {
  display: flex;
  flex: 1;
  justify-content: center;
  text-decoration: none;
  height: 100%;
  align-items: center;
}

.logo h1 {
  font-size: 1.1rem;
  font-weight: 500;
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #0051c3;
}

.sidebar-toggle {
  cursor: pointer;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6b7280;
  border-radius: 4px;
  transition: all 0.2s ease;
  position: absolute;
  right: 8px;
}

/* 在折叠状态下隐藏侧边栏内的折叠按钮 */
.el-aside[style*="width: 64px"] .sidebar-toggle {
  display: none;
}

.sidebar-toggle:hover {
  color: #0051c3;
  background-color: #f0f7ff;
}

.sidebar-menu {
  flex: 1;
  overflow-y: auto;
  border-right: none;
  background-color: #ffffff;
  padding: 12px 0;
}

.sidebar-menu .el-menu-item {
  display: flex;
  align-items: center;
  height: 48px;
  line-height: 48px;
  color: #4b5563;
  margin: 4px 10px;
  padding-left: 16px !important;
  border-left: 3px solid transparent;
  font-size: 0.9rem;
  border-radius: 6px;
  transition: all 0.25s ease;
}

.sidebar-menu .el-menu-item.is-active {
  background: linear-gradient(to right, #f0f7ff, #f9fbff);
  color: #0051c3;
  border-left: 3px solid #0051c3;
  font-weight: 500;
  box-shadow: 0 2px 5px rgba(0, 0, 0, 0.03);
}

.sidebar-menu .el-menu-item:hover {
  background-color: #f0f7ff;
  color: #0051c3;
  transform: translateX(2px);
}

.sidebar-menu .el-icon {
  margin-right: 14px;
  font-size: 18px;
  transition: all 0.25s ease;
}

.sidebar-menu .el-menu-item:hover .el-icon {
  transform: scale(1.1);
}

.sidebar-footer {
  border-top: 1px solid #f0f7ff;
  margin-top: auto;
  padding: 12px 0;
}

.sidebar-footer .footer-item {
  color: #6b7280;
  display: flex;
  align-items: center;
  height: 44px;
  line-height: 44px;
  margin: 4px 10px;
  padding-left: 16px;
  border-radius: 6px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.25s ease;
  text-decoration: none;
}

.sidebar-footer .footer-item .el-icon {
  margin-right: 14px;
  font-size: 18px;
}

/* 折叠状态下的底部图标样式 */
.el-aside[style*="width: 64px"] .sidebar-footer .footer-item {
  padding-left: 0;
  justify-content: center;
}

.el-aside[style*="width: 64px"] .sidebar-footer .footer-item .el-icon {
  margin-right: 0;
  font-size: 18px;
}

.el-aside[style*="width: 64px"] .sidebar-footer .footer-item span {
  display: none;
}

.sidebar-footer .footer-item:hover {
  background: linear-gradient(to right, #f0f7ff, #f9fbff);
  color: #0051c3;
  transform: translateX(2px);
}

.el-aside[style*="width: 64px"] .sidebar-footer .footer-item:hover {
  transform: scale(1.1);
}

/* 头部内容样式 */
.header-content {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  height: 100%;
}

.left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.breadcrumb {
  font-size: 14px;
  font-weight: 500;
}

.auth-buttons {
  display: flex;
  gap: 8px;
}

.user-profile {
  display: flex;
  align-items: center;
  cursor: pointer;
  padding: 2px 8px;
  border-radius: 4px;
  transition: background-color 0.3s;
}

.user-profile:hover {
  background-color: rgba(0, 0, 0, 0.05);
}

.avatar {
  margin-right: 8px;
}

.username {
  font-size: 14px;
  color: #333;
  margin-right: 4px;
}

/* 页面过渡动画 */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.3s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

/* 页脚样式 */
.footer {
  font-size: 0.9rem;
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.footer p {
  font-size: 14px;
  color: #6b7280;
  position: relative;
  padding: 0 20px;
  margin: 0;
}

.footer p::before,
.footer p::after {
  content: '';
  position: absolute;
  top: 50%;
  width: 30px;
  height: 1px;
  background-color: #e5e7eb;
}

.footer p::before {
  left: -20px;
}

.footer p::after {
  right: -20px;
}

/* Element Plus 样式覆盖 */
.el-button--primary {
  background-color: #0051c3;
  border-color: #0051c3;
}

.el-button--primary:hover,
.el-button--primary:focus {
  background-color: #0046ad;
  border-color: #0046ad;
}

.el-card {
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  box-shadow: 0 1px 3px rgba(0,0,0,0.05);
  margin-bottom: 24px;
}

.el-card__header {
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
  font-weight: 500;
}

.el-form-item__label {
  font-weight: 500;
}

.el-input-number.is-controls-right .el-input__wrapper {
  padding-right: 35px;
}

.el-select .el-input .el-select__caret {
  color: #606266;
}

.el-tag {
  border-radius: 4px;
}

/* 折叠菜单样式 */
.el-menu--collapse {
  width: 64px;
}

.el-menu--collapse .el-menu-item {
  padding-left: 20px !important;
  justify-content: center;
}

.el-menu--collapse .el-menu-item .el-icon {
  margin-right: 0 !important;
}


.sidebar-menu::-webkit-scrollbar {
  width: 4px;
}

.sidebar-menu::-webkit-scrollbar-track {
  background: transparent;
}

.sidebar-menu::-webkit-scrollbar-thumb {
  background-color: rgba(0, 0, 0, 0.1);
  border-radius: 4px;
}

/* 折叠状态下隐藏滚动条*/
.el-aside[style*="width: 64px"] .sidebar-menu,
.el-menu--collapse + .sidebar-menu,
.el-menu.el-menu--collapse,
.el-aside[style*="width: 64px"] .sidebar,
.el-aside[style*="width:64px"] .sidebar-menu {
  overflow: hidden !important;
  scrollbar-width: none !important; /* Firefox */
  -ms-overflow-style: none !important; /* IE and Edge */
}

.el-aside[style*="width: 64px"] .sidebar-menu::-webkit-scrollbar,
.el-menu--collapse + .sidebar-menu::-webkit-scrollbar,
.el-menu.el-menu--collapse::-webkit-scrollbar,
.el-aside[style*="width: 64px"] .sidebar::-webkit-scrollbar,
.el-aside[style*="width:64px"] .sidebar-menu::-webkit-scrollbar {
  width: 0 !important;
  display: none !important; /* Chrome, Safari, Edge */
}

/* 直接覆盖Element Plus的样式 */
.el-menu--vertical.el-menu--collapse {
  overflow: hidden !important;
}

/* 添加到全局样式部分 */
html body .el-aside[style*="width: 64px"] .sidebar * {
  overflow: hidden !important;
}

/* 菜单项提示框样式 */
.el-menu--popup {
  min-width: 140px;
  padding: 8px 0;
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.el-menu--popup .el-menu-item {
  height: 40px;
  line-height: 40px;
  margin: 4px 8px;
  border-radius: 4px;
  font-size: 14px;
}

.el-menu--popup .el-menu-item:hover {
  background-color: #f0f7ff;
}

.el-menu--popup .el-menu-item.is-active {
  background-color: #f0f7ff;
  color: #0051c3;
}
</style> 