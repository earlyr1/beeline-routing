import type { ComponentType } from 'react';
import { displayedPlan } from '../../lib/planView';
import type { AppState } from '../../store/useAppStore';
import { BrigadesTab } from './BrigadesTab';
import { ComparisonTab } from './ComparisonTab';
import { ProposalsTab } from './ProposalsTab';
import { RequestsTab } from './RequestsTab';
import { TimelineTab } from './TimelineTab';
import { UnassignedTab } from './UnassignedTab';

export interface PanelTab {
  id: string;
  title: string;
  component: ComponentType;
  /** Число в бейдже вкладки; null скрывает бейдж */
  badge?: (app: AppState) => number | null;
}

export const PANEL_TABS: PanelTab[] = [
  { id: 'requests', title: 'Заявки', component: RequestsTab },
  { id: 'brigades', title: 'Бригады', component: BrigadesTab },
  { id: 'timeline', title: 'Таймлайн', component: TimelineTab },
  {
    id: 'unassigned',
    title: 'Неназначенные',
    component: UnassignedTab,
    badge: (app) => (app.state ? displayedPlan(app.state, app.showPrevious).unassigned.length || null : null),
  },
  { id: 'comparison', title: 'Сравнение', component: ComparisonTab },
  { id: 'proposals', title: 'Рекомендуемые изменения', component: ProposalsTab },
];
