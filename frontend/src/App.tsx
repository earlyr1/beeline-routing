import { useEffect } from 'react';
import { MainScreen } from './components/MainScreen';
import { UploadScreen } from './components/UploadScreen';
import { useAppStore } from './store/useAppStore';

export function App() {
  const loadConfig = useAppStore((s) => s.loadConfig);
  const hasPlan = useAppStore((s) => s.state !== null);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  return hasPlan ? <MainScreen /> : <UploadScreen />;
}
