<template>
  <div class="account-pool">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>账号池</span>
          <div>
            <el-input v-model="form.phone" placeholder="手机号" style="width:150px;margin-right:8px" />
            <el-input v-model="form.password" placeholder="密码" type="password" style="width:150px;margin-right:8px" />
            <el-input v-model="form.note" placeholder="备注" style="width:110px;margin-right:8px" />
            <el-button type="primary" @click="addAccount">添加</el-button>
          </div>
        </div>
      </template>

      <el-table :data="accounts" v-loading="loading" style="width:100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="phone" label="手机号" width="140" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="scope">
            <el-tag :type="tagType(scope.row.status)">{{ scope.row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" />
        <el-table-column label="Cookie" width="90">
          <template #default="scope">{{ scope.row.has_cookie ? '有' : '无' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="230">
          <template #default="scope">
            <el-button size="small" type="primary" @click="startLogin(scope.row.id)">登录</el-button>
            <el-button size="small" @click="checkAccount(scope.row.id)">检测</el-button>
            <el-button size="small" type="danger" @click="removeAccount(scope.row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header>
        <span>登录会话</span>
        <span style="margin-left:12px;color:#909399">{{ sessionText }}</span>
      </template>
      <div style="display:flex;gap:8px;align-items:center">
        <el-input v-model="smsCode" placeholder="6 位短信码" maxlength="6" style="width:160px" />
        <el-button type="primary" @click="submitSms" :disabled="!needSms">提交短信码</el-button>
        <span style="color:#909399;font-size:12px">
          浏览器跑在服务器上（无头），这里只负责输入短信码
        </span>
      </div>
    </el-card>

    <el-card style="margin-top:16px">
      <template #header>
        <span>采集</span>
      </template>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <el-input v-model="crawl.cities" placeholder="bj,sh,gz" style="width:180px" />
        <el-input v-model="crawl.pages" placeholder="2-4" style="width:110px" />
        <el-input v-model.number="crawl.workers" placeholder="并发" style="width:90px" />
        <el-button type="primary" @click="startCrawl">启动采集</el-button>
        <el-button @click="pollCrawl">查看进度</el-button>
        <span style="color:#909399">{{ crawlText }}</span>
      </div>
    </el-card>
  </div>
</template>

<script>
import api from '@/api/api';

export default {
  name: 'AccountPool',
  data() {
    return {
      accounts: [],
      loading: false,
      form: { phone: '', password: '', note: '' },
      loginId: null,
      session: { state: 'idle', message: '' },
      smsCode: '',
      timer: null,
      crawl: { cities: 'bj,sh,gz', pages: '2-4', workers: 3, headless: true },
      crawlStatus: null,
    };
  },
  computed: {
    sessionText() {
      return `${this.session.state} ${this.session.message || ''}`;
    },
    needSms() {
      return this.session.state === 'waiting_sms';
    },
    crawlText() {
      const s = this.crawlStatus;
      if (!s) return '';
      if (s.running) return `运行中 ${s.seconds}s`;
      return s.stats ? JSON.stringify(s.stats) : '';
    },
  },
  mounted() {
    this.loadAccounts();
  },
  beforeUnmount() {
    if (this.timer) clearInterval(this.timer);
  },
  methods: {
    async loadAccounts() {
      this.loading = true;
      try {
        const { data } = await api.get('/accounts');
        this.accounts = data.accounts || [];
      } finally {
        this.loading = false;
      }
    },
    async addAccount() {
      if (!this.form.phone || !this.form.password) return;
      await api.post('/accounts', this.form);
      this.form = { phone: '', password: '', note: '' };
      this.loadAccounts();
    },
    async removeAccount(id) {
      await api.delete(`/accounts/${id}`);
      this.loadAccounts();
    },
    async startLogin(id) {
      this.loginId = id;
      const { data } = await api.post(`/accounts/${id}/login/start`, null, { params: { headless: true } });
      this.session = data;
      if (this.timer) clearInterval(this.timer);
      this.timer = setInterval(this.pollLogin, 3000);
    },
    async pollLogin() {
      if (!this.loginId) return;
      const { data } = await api.get(`/accounts/${this.loginId}/login/status`);
      this.session = data;
      if (data.done) {
        clearInterval(this.timer);
        this.timer = null;
        this.loadAccounts();
      }
    },
    async submitSms() {
      if (!this.loginId || !this.smsCode) return;
      await api.post(`/accounts/${this.loginId}/login/sms`, { code: this.smsCode });
      this.smsCode = '';
      this.pollLogin();
    },
    async checkAccount(id) {
      const { data } = await api.post(`/accounts/${id}/check`);
      this.$message && this.$message.info(`${data.status} ${data.message}`);
      this.loadAccounts();
    },
    async startCrawl() {
      await api.post('/crawl/start', this.crawl);
      this.pollCrawl();
    },
    async pollCrawl() {
      const { data } = await api.get('/crawl/status');
      this.crawlStatus = data;
    },
    tagType(status) {
      return { ok: 'success', expired: 'warning', failed: 'danger', needs_manual: 'warning' }[status] || 'info';
    },
  },
};
</script>

<style scoped>
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
