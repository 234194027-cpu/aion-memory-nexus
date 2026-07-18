/* WP-0A-T02: ESLint 基线配置
 *
 * 目标：先建立可运行的 ESLint 基线，不一次性格式化所有文件。
 * 后续工作包可逐步收紧规则。
 *
 * 规则说明：
 *   - 'no-unused-vars': 'warn'  先基线化，不阻塞构建
 *   - 'vue/multi-word-component-names': 'off'  允许 index.vue 等单单词文件名
 */
module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  extends: [
    'eslint:recommended',
    'plugin:vue/vue3-essential',
    '@vue/eslint-config-typescript',
  ],
  parserOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
  },
  rules: {
    'no-unused-vars': 'warn',
    'vue/multi-word-component-names': 'off',
  },
  ignorePatterns: [
    'dist/',
    'node_modules/',
    '*.config.js',
    '*.config.ts',
  ],
}
