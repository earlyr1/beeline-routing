import { useEffect } from 'react';
import { MainScreen } from './components/MainScreen';
import { UploadScreen } from './components/UploadScreen';
import { useAppStore } from './store/useAppStore';

export function App() {
  const loadConfig = useAppStore((s) => s.loadConfig);
  const restoreSession = useAppStore((s) => s.restoreSession);
  const hasPlan = useAppStore((s) => s.state !== null);

  useEffect(() => {
    void loadConfig();
    void restoreSession();
  }, [loadConfig, restoreSession]);

  return hasPlan ? <MainScreen /> : <UploadScreen />;
}
