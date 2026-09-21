import { useShallow } from 'zustand/react/shallow';
import { useAppStore } from '../../store/useAppStore';
import { ExplanationCard } from '../ExplanationCard';
import { RouteCard } from '../RouteCard';
import { PANEL_TABS } from './tabs';

export function RightPanel() {
  // Панель берёт из стора только вкладку и числа бейджей: на каждом шаге часов она не перерисовывается целиком.
  const activeTab = useAppStore((s) => s.activeTab);
  const setTab = useAppStore((s) => s.setTab);
  const badges = useAppStore(useShallow((s) => PANEL_TABS.map((item) => item.badge?.(s) ?? null)));
  // Вкладка, которой нет в панели, открывает «Заявки».
  const tab = PANEL_TABS.find((item) => item.id === activeTab) ?? PANEL_TABS[0];
  const Active = tab.component;
  return (
    <aside className="panel">
      <ExplanationCard />
      <RouteCard />
      <nav className="tabs" role="tablist">
        {PANEL_TABS.map((item, index) => {
          const badge = badges[index];
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === tab.id}
              className={`tabs__tab${item.id === tab.id ? ' tabs__tab--active' : ''}`}
              onClick={() => setTab(item.id)}
            >
              {item.title}
              {badge ? (
                <span className="tabs__badge" title={item.badgeTitle}>
                  {badge}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>
      <div className="panel__content" role="tabpanel">
        <Active />
      </div>
    </aside>
  );
}
