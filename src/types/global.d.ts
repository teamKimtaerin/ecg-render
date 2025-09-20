// Global type declarations for browser window extensions

declare global {
  interface Window {
    pageReady?: boolean;
    loadMotionTextScenario?: (scenario: any) => Promise<{ success: boolean; error?: string; cueCount?: number }>;
    seekToTime?: (time: number) => Promise<void>;
    cleanup?: () => void;
    getRendererStatus?: () => any;
  }
}

export {};