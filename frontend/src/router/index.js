import { createRouter, createWebHistory } from 'vue-router'
import i18n from '../i18n'

// 导入页面组件
import Home from '../views/Home.vue'
import TaskList from '../views/TaskList.vue'
import HouseList from '../views/HouseList.vue'
import Analysis from '../views/Analysis.vue'
import NotFound from '../views/NotFound.vue'
import Login from '../views/Login.vue'
import Register from '../views/Register.vue'
import ForgotPassword from '../views/ForgotPassword.vue'
import ResetPassword from '../views/ResetPassword.vue'
import UserProfile from '../views/UserProfile.vue'
import UserSettings from '../views/UserSettings.vue'
import SystemSettings from '../views/SystemSettings.vue'
import ScheduledTasks from '../views/ScheduledTasks.vue'
import IpManagement from '../views/IpManagement.vue'
import AccountPool from '../views/AccountPool.vue'

const routes = [
  {
    path: '/',
    name: 'Home',
    component: Home,
    meta: { titleKey: 'nav.home' }
  },
  {
    path: '/task-list',
    name: 'TaskList',
    component: TaskList,
    meta: { titleKey: 'nav.taskList', requiresAuth: true }
  },
  {
    path: '/account-pool',
    name: 'AccountPool',
    component: AccountPool,
    meta: { titleKey: 'menu.accountPool', requiresAuth: true },
  },
  {
    path: '/ip-management',
    name: 'IpManagement',
    component: IpManagement,
    meta: { titleKey: 'menu.ipManagement', requiresAuth: true }
  },
  {
    path: '/scheduled-tasks',
    name: 'ScheduledTasks',
    component: ScheduledTasks,
    meta: { titleKey: 'nav.scheduledTasks', requiresAuth: true }
  },
  {
    path: '/tasks/:taskId/houses',
    name: 'HouseList',
    component: HouseList,
    meta: { titleKey: 'nav.houseList', requiresAuth: true }
  },
  {
    path: '/house-list',
    name: 'houseList',
    component: HouseList,
    meta: { titleKey: 'nav.houseList', requiresAuth: true }
  },
  {
    path: '/analysis',
    name: 'Analysis',
    component: Analysis,
    meta: { titleKey: 'nav.analysis', requiresAuth: true }
  },
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { titleKey: 'auth.login' }
  },
  {
    path: '/register',
    name: 'Register',
    component: Register,
    meta: { titleKey: 'auth.register' }
  },
  {
    path: '/forgot-password',
    name: 'ForgotPassword',
    component: ForgotPassword,
    meta: { titleKey: 'auth.forgotPassword' }
  },
  {
    path: '/reset-password',
    name: 'ResetPassword',
    component: ResetPassword,
    meta: { titleKey: 'auth.resetPassword' }
  },
  {
    path: '/user/profile',
    name: 'UserProfile',
    component: UserProfile,
    meta: { titleKey: 'user.profile', requiresAuth: true }
  },
  {
    path: '/user/settings',
    name: 'UserSettings',
    component: UserSettings,
    meta: { titleKey: 'user.settings', requiresAuth: true }
  },
  {
    path: '/settings',
    name: 'SystemSettings',
    component: SystemSettings,
    meta: { titleKey: 'menu.settings', requiresAuth: true }
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: NotFound,
    meta: { titleKey: 'notFound' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫，用于设置页面标题
router.beforeEach((to, from, next) => {
  // 设置文档标题
  document.title = i18n.global.t(to.meta.titleKey) || '租房数据分析系统'
  
  // 检查是否需要身份验证
  if (to.matched.some(record => record.meta.requiresAuth)) {
    // 检查用户是否已登录
    const isLoggedIn = localStorage.getItem('token')
    
    if (!isLoggedIn) {
      // 未登录，重定向到登录页
      next({
        path: '/login',
        query: { redirect: to.fullPath }
      })
    } else {
      next()
    }
  } else {
    next()
  }
})

export default router 