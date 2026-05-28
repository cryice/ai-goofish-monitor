<template>
  <div class="container mx-auto py-6">
    <div class="mb-6">
      <h1 class="text-2xl font-bold text-gray-800">{{ t('collectedData.title') }}</h1>
      <p class="text-gray-600 mt-2">{{ t('collectedData.description') }}</p>
    </div>

    <div class="bg-white rounded-lg shadow p-6 mb-6">
      <div class="flex flex-wrap gap-4 items-center mb-4">
        <div class="flex-1 min-w-[200px]">
          <label class="block text-sm font-medium text-gray-700 mb-1">{{ t('collectedData.filters.keyword') }}</label>
          <input
            v-model="filters.keyword"
            type="text"
            :placeholder="t('collectedData.filters.keywordPlaceholder')"
            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
        <div class="flex-1 min-w-[200px]">
          <label class="block text-sm font-medium text-gray-700 mb-1">{{ t('collectedData.filters.status') }}</label>
          <select
            v-model="filters.status"
            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">{{ t('collectedData.filters.all') }}</option>
            <option value="recommended">{{ t('collectedData.filters.recommended') }}</option>
            <option value="not_recommended">{{ t('collectedData.filters.notRecommended') }}</option>
            <option value="pending">{{ t('collectedData.filters.pending') }}</option>
          </select>
        </div>
        <div class="flex-1 min-w-[200px]">
          <label class="block text-sm font-medium text-gray-700 mb-1">{{ t('collectedData.filters.category') }}</label>
          <select
            v-model="filters.category"
            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">{{ t('collectedData.filters.all') }}</option>
            <option value="充值服务">充值服务</option>
            <option value="成品号">成品号</option>
            <option value="订阅服务">订阅服务</option>
            <option value="安装教程">安装教程</option>
            <option value="镜像站">镜像站</option>
            <option value="共享账号">共享账号</option>
            <option value="求购信息">求购信息</option>
            <option value="其他">其他</option>
          </select>
        </div>
        <div class="flex items-end">
          <button
            @click="fetchData"
            class="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {{ t('collectedData.filters.search') }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="loading" class="flex justify-center items-center py-12">
      <div class="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-blue-500"></div>
    </div>

    <div v-else-if="error" class="bg-red-50 border-l-4 border-red-400 p-4 mb-6">
      <p class="text-red-700">{{ error }}</p>
    </div>

    <div v-else-if="items.length === 0" class="bg-white rounded-lg shadow p-6 text-center text-gray-500">
      {{ t('collectedData.noData') }}
    </div>

    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      <div 
        v-for="item in items" 
        :key="item.商品信息.商品ID" 
        class="border rounded-lg overflow-hidden shadow-md hover:shadow-lg transition-shadow duration-300"
      >
        <div class="p-4">
          <!-- 商品图片 -->
          <div v-if="item.商品信息.商品图片列表 && item.商品信息.商品图片列表[0]" class="mb-3">
            <img 
              :src="item.商品信息.商品图片列表[0]" 
              :alt="item.商品信息.商品标题" 
              class="w-full h-48 object-cover rounded"
              @error="onImageError"
            />
          </div>
          
          <!-- 商品标题 -->
          <h3 class="font-semibold text-lg mb-2 line-clamp-2" :title="item.商品信息.商品标题">{{ item.商品信息.商品标题 }}</h3>
          
          <!-- 商品分类标签 -->
          <div class="mb-2">
            <span 
              v-for="(category, index) in item['商品分类']" 
              :key="index"
              :class="getCategoryClass(category)"
              class="inline-block px-2 py-1 text-xs rounded mr-2 mb-1"
            >
              {{ category }}
            </span>
          </div>
          
          <!-- 价格信息 -->
          <div class="text-2xl font-bold text-red-600 mb-2">¥{{ formatPrice(item.商品信息.当前售价) }}</div>
          
          <!-- SKU信息 -->
          <div v-if="getSkuList(item).length > 0" class="mb-2">
            <details class="group">
              <summary class="text-sm text-blue-600 cursor-pointer hover:text-blue-800 flex items-center">
                <span>查看SKU选项 ({{ getSkuList(item).length }})</span>
                <svg class="ml-1 w-4 h-4 transition-transform group-open:rotate-180" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                </svg>
              </summary>
              <div class="mt-2 space-y-1">
                <div v-for="(sku, index) in getSkuList(item)" :key="index" class="text-sm text-gray-600 pl-2 border-l-2 border-gray-200">
                  <span class="font-medium">{{ sku.sku_name }}</span>
                  <span v-if="sku.sku_price" class="text-red-600">: ¥{{ sku.sku_price }}</span>
                </div>
              </div>
            </details>
          </div>
          
          <!-- 卖家信息 -->
          <div class="text-sm text-gray-600 mb-2">
            <div class="flex items-center">
              <UserIcon class="w-4 h-4 mr-1" />
              <span>{{ item.卖家信息.卖家昵称 }}</span>
            </div>
            <div class="flex items-center mt-1">
              <ClockIcon class="w-4 h-4 mr-1" />
              <span>{{ item.爬取时间 ? formatDate(item.爬取时间) : t('common.unknown') }}</span>
            </div>
          </div>
          
          <!-- AI分析结果 -->
          <div class="mt-3 p-2 rounded bg-gray-50">
            <div class="flex items-center text-sm">
              <InfoIcon class="w-4 h-4 mr-1" />
              <span 
                :class="{
                  'text-green-600': item.ai_analysis?.is_recommended === true,
                  'text-red-600': item.ai_analysis?.is_recommended === false,
                  'text-gray-600': item.ai_analysis?.is_recommended === null || item.ai_analysis?.is_recommended === undefined
                }"
              >
                {{
                  item.ai_analysis?.is_recommended === true ? t('collectedData.aiRecommendations.recommended') :
                  item.ai_analysis?.is_recommended === false ? t('collectedData.aiRecommendations.notRecommended') :
                  t('collectedData.aiRecommendations.pending')
                }}
              </span>
            </div>
            <div v-if="item.ai_analysis?.reason" class="mt-1 text-xs text-gray-500 truncate" :title="item.ai_analysis.reason">
              {{ item.ai_analysis.reason }}
            </div>
          </div>
          
          <!-- 操作按钮 -->
          <div class="mt-4 flex space-x-2">
            <a 
              :href="item.商品信息.商品链接" 
              target="_blank" 
              rel="noopener noreferrer"
              class="flex-1 text-center px-3 py-2 bg-blue-100 text-blue-700 rounded hover:bg-blue-200 text-sm"
            >
              {{ t('collectedData.viewOriginal') }}
            </a>
            <button 
              @click="copyLink(item.商品信息.商品链接)"
              class="px-3 py-2 bg-gray-100 text-gray-700 rounded hover:bg-gray-200 text-sm"
              :title="t('collectedData.copyLink')"
            >
              <ClipboardIcon class="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="pagination.total > pagination.limit" class="mt-6 flex justify-center">
      <nav class="inline-flex rounded-md shadow">
        <button
          @click="goToPage(pagination.current - 1)"
          :disabled="pagination.current <= 1"
          class="px-3 py-2 rounded-l-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ t('common.previous') }}
        </button>
        
        <span class="px-4 py-2 border-t border-b border-gray-300 bg-white text-sm font-medium text-gray-700">
          {{ t('common.pageXOfY', { current: pagination.current, total: pagination.pages }) }}
        </span>
        
        <button
          @click="goToPage(pagination.current + 1)"
          :disabled="pagination.current >= pagination.pages"
          class="px-3 py-2 rounded-r-md border border-gray-300 bg-white text-sm font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {{ t('common.next') }}
        </button>
      </nav>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import { User as UserIcon, Clock as ClockIcon, Info as InfoIcon, Clipboard as ClipboardIcon } from 'lucide-vue-next';

// 类型定义
interface SKUItem {
  sku_name: string;
  sku_price: string | number;
}

interface ResultItem {
  商品信息: {
    商品ID: string;
    商品标题: string;
    当前售价: number;
    商品图片列表?: string[];
    商品链接: string;
    SKU列表?: SKUItem[];
  };
  卖家信息: {
    卖家昵称: string;
  };
  爬取时间: string;
  ai_analysis?: {
    is_recommended?: boolean | null;
    reason?: string;
  };
  SKU列表?: SKUItem[];
  商品分类?: string[];
}

// 国际化
const { t } = useI18n();

// 状态
const loading = ref(false);
const error = ref('');
const items = ref<ResultItem[]>([]);
const pagination = reactive({
  current: 1,
  total: 0,
  limit: 12,
  pages: 0
});

// 过滤器
const filters = reactive({
  keyword: '',
  status: '',
  category: ''
});

// 获取数据
const fetchData = async () => {
  loading.value = true;
  error.value = '';
  
  try {
    // 从后端获取所有采集的数据
    const queryParams = new URLSearchParams({
      page: pagination.current.toString(),
      limit: pagination.limit.toString(),
    });
    
    if (filters.keyword) {
      queryParams.append('keyword', filters.keyword);
    }
    
    if (filters.status) {
      queryParams.append('status', filters.status);
    }
    
    if (filters.category) {
      queryParams.append('category', filters.category);
    }
    
    const response = await fetch(`/api/results/all-data?${queryParams}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      }
    });
    
    if (!response.ok) {
      throw new Error(t('collectedData.errors.fetchFailed'));
    }
    
    const data = await response.json();
    items.value = data.items || [];
    pagination.total = data.total_items || 0;
    pagination.pages = data.total_pages || 0;
  } catch (err) {
    console.error('Error fetching collected data:', err);
    error.value = t('collectedData.errors.fetchFailed');
  } finally {
    loading.value = false;
  }
};

// 页面切换
const goToPage = (page: number) => {
  if (page < 1 || page > pagination.pages) return;
  pagination.current = page;
  fetchData();
};

// 日期格式化
const formatDate = (dateString: string) => {
  const date = new Date(dateString);
  return date.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

// 图片加载失败处理
const onImageError = (e: Event) => {
  const target = e.target as HTMLImageElement;
  target.src = '/placeholder-image.svg'; // 使用占位图
};

// 复制链接
const copyLink = async (link: string) => {
  try {
    await navigator.clipboard.writeText(link);
    // 这里可以添加提示信息
  } catch (err) {
    console.error('Failed to copy link:', err);
  }
};

// 获取分类标签的颜色样式
const getCategoryClass = (category: string) => {
  const categoryColors: Record<string, string> = {
    '充值服务': 'bg-blue-100 text-blue-800',
    '成品号': 'bg-green-100 text-green-800',
    '订阅服务': 'bg-purple-100 text-purple-800',
    '安装教程': 'bg-yellow-100 text-yellow-800',
    '镜像站': 'bg-red-100 text-red-800',
    '共享账号': 'bg-indigo-100 text-indigo-800',
    '求购信息': 'bg-pink-100 text-pink-800',
    '其他': 'bg-gray-100 text-gray-800',
  };
  
  return categoryColors[category] || 'bg-gray-100 text-gray-800';
};

// 格式化价格，去除可能存在的货币符号
const formatPrice = (price: string | number) => {
  if (typeof price === 'number') {
    return price.toString();
  }
  
  if (typeof price === 'string') {
    // 去除可能存在的货币符号
    return price.replace(/[¥$€£]/g, '').trim();
  }
  
  return String(price);
};

const getSkuList = (item: ResultItem): SKUItem[] => {
  return item.SKU列表 || item.商品信息.SKU列表 || [];
};

// 组件挂载时获取数据
onMounted(() => {
  fetchData();
});
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
