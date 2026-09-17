import { useEffect } from 'react';
import { MainScreen } from './components/MainScreen';
import { UploadScreen } from './components/UploadScreen';
import { useAppStore } from './store/useAppStore';

export function App() {
  const loadConfig = useAppStore((s) => s.loadConfig);
  const restoreSession = useAppStore((s) => s.restoreSession);
  const hasPlan = useAppStore((s) => s.state !== null);
  const restoring = useAppStore((s) => s.restoring);

  useEffect(() => {
    void loadConfig();
    void restoreSession();
  }, [loadConfig, restoreSession]);

  if (hasPlan) return <MainScreen />;
  if (restoring) {
    return (
      <main className="upload-screen" aria-busy="true">
        <p className="muted">Открываем прежний план…</p>
      </main>
    );
  }
  return <UploadScreen />;
}
