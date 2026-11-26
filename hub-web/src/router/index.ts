import { createRouter, createWebHistory } from 'vue-router';

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'feed',
      component: () => import('@/views/FeedView.vue'),
      meta: { title: '精选史库广场 · Sowing Discord Hub' },
    },
    {
      path: '/m/:id',
      name: 'detail',
      component: () => import('@/views/ItemDetailView.vue'),
      meta: { title: '史料详情 · Sowing Discord Hub' },
    },
    {
      path: '/submit',
      name: 'submit',
      component: () => import('@/views/SubmitView.vue'),
      meta: { title: '真人投稿 · Sowing Discord Hub' },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('@/views/AdminView.vue'),
      meta: { title: '极速审核台 · Sowing Discord Hub' },
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
  scrollBehavior() {
    return { top: 0 };
  },
});

router.afterEach((to) => {
  if (typeof to.meta?.title === 'string') {
    document.title = to.meta.title;
  }
});

export default router;
