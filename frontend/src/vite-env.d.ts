/// <reference types="vite/client" />

/**
 * Environment variable type definitions for Vite.
 * Provides type safety when accessing import.meta.env values.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_APP_TITLE: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
