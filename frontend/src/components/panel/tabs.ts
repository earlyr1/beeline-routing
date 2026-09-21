import type { ComponentType } from 'react';
import { callList } from '../../lib/communications';
import type { AppState } from '../../store/useAppStore';
import { BrigadesTab } from './BrigadesTab';
import { CommunicationsTab } from './CommunicationsTab';
import { ComparisonTab } from './ComparisonTab';
import { ProposalsTab } from './ProposalsTab';
import { RequestsTab } from './RequestsTab';
import { TimelineTab } from './TimelineTab';

export interface PanelTab {
  id: string;
  title: string;
  component: ComponentType;
  /** Число в бейдже вкладки; null скрывает бейдж */
  badge?: (app: AppState) => number | null;
  /** Подсказка к бейджу: что он считает */
  badgeTitle?: string;
}

export const PANEL_TABS: PanelTab[] = [
  {
    id: 'requests',
    title: 'Заявки',
    component: RequestsTab,
    // Заявки без бригады видны, не открывая вкладку; внутри то же число стоит на фильтре «Без исполнителя».
    badge: (app) => (app.state ? app.state.plan.unassigned.length || null : null),
    badgeTitle: 'Заявки без исполнителя',
  },
  { id: 'brigades', title: 'Бригады', component: BrigadesTab },
  { id: 'timeline', title: 'Таймлайн', component: TimelineTab },
  { id: 'comparison', title: 'Сравнение', component: ComparisonTab },
  { id: 'proposals', title: 'Рекомендуемые изменения', component: ProposalsTab },
  {
    id: 'communications',
    title: 'Коммуникации',
    component: CommunicationsTab,
    // В бейдже — сколько клиентов ждут звонка: согласованные строки в него не входят.
    badge: (app) => (app.state ? callList(app.state, app.agreed, app.clock).pending.length || null : null),
  },
];
