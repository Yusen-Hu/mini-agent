<template>
  <router-view />
  <Toast />
</template>

<script setup>
import Toast from './components/Toast.vue'
</script>

<style>
:root {
  --primary: #1a73e8;
  --danger: #e53935;
  --text: #333;
  --border: #eee;
  --bg: #f5f5f5;
  --ai-bubble: #f8f9fa;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: var(--bg); height: 100vh; }
</style>
