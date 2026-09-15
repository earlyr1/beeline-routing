import { useAppStore } from '../../store/useAppStore';
import { ExplanationCard } from '../ExplanationCard';
import { RouteCard } from '../RouteCard';
import { PANEL_TABS } from './tabs';

export function RightPanel() {
  const app = useAppStore();
  const tab = PANEL_TABS.find((item) => item.id === app.activeTab) ?? PANEL_TABS[0];
  const Active = tab.component;
  return (
    <aside className="panel">
      <ExplanationCard />
      <RouteCard />
      <nav className="tabs" role="tablist">
        {PANEL_TABS.map((item) => {
          const badge = item.badge?.(app) ?? null;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={item.id === tab.id}
              className={`tabs__tab${item.id === tab.id ? ' tabs__tab--active' : ''}`}
              onClick={() => app.setTab(item.id)}
            >
              {item.title}
              {badge ? <span className="tabs__badge">{badge}</span> : null}
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
