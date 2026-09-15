import type {
  DatasetStage,
  EventType,
  GeocodePrecision,
  HHMM,
  MatrixSource,
  Priority,
  ReasonCode,
  Skill,
  SolverName,
  Transport,
} from '../api/types';

export const SKILL_LABELS: Record<Skill, string> = {
  local: 'Локальные работы',
  connection: 'Работы на подключение и дозаказы',
  emergency: 'Аварийные работы',
};

export const TRANSPORT_LABELS: Record<Transport, string> = {
  car: 'Автомобиль',
  foot: 'Пешеход',
  bike: 'Велосипед',
  public: 'Общественный транспорт',
};

export const PRIORITY_LABELS: Record<Priority, string> = { normal: 'Обычная', urgent: 'Срочная' };

export const STAGE_LABELS: Record<DatasetStage, string> = {
  parsing: 'Чтение файла',
  geocoding: 'Поиск адресов на карте',
  matrix: 'Расчёт расстояний',
  solving: 'Расчёт планов',
  ready: 'Готово',
};

export const SOLVER_LABELS: Record<SolverName, string> = {
  fcfs: 'Базовый (FCFS)',
  ortools: 'Оптимизированный',
  dispatchers: 'Диспетчеры',
};

export const EVENT_LABELS: Record<EventType, string> = {
  urgent: 'Срочная заявка',
  cancel: 'Отмена заявки',
  restore: 'Возврат заявки',
  engineer_unavailable: 'Инженер недоступен',
};

export const REASON_LABELS: Record<ReasonCode, string> = {
  no_skill: 'Нет навыка',
  no_transport: 'Нет транспорта',
  does_not_fit_window_or_shift: 'Не помещается в окно или смену',
  no_free_engineer_in_window: 'Нет свободных исполнителей',
  address_not_found: 'Адрес не найден',
};

export const PRECISION_LABELS: Record<GeocodePrecision, string> = {
  house: 'до дома',
  street: 'до улицы',
  locality: 'до района',
  none: 'не найдено',
};

export const MATRIX_SOURCE_LABELS: Record<MatrixSource, string> = {
  osrm: 'Дорожный граф OSRM',
  haversine: 'Прямые расстояния (OSRM недоступен)',
};

// Часы не ограничены 24: в плане диспетчеров встречаются значения вроде 25:53 и 49:13.
const TIME_RE = /^(\d+):(\d{2})$/;

export function isValidTime(value: string): boolean {
  const match = TIME_RE.exec(value.trim());
  return match !== null && Number(match[2]) < 60;
}

export function toMinutes(value: HHMM): number {
  const match = TIME_RE.exec(value.trim());
  if (!match || Number(match[2]) >= 60) {
    throw new Error(`Некорректное время: ${value}`);
  }
  return Number(match[1]) * 60 + Number(match[2]);
}

export function fromMinutes(total: number): HHMM {
  const safe = Math.max(0, Math.round(total));
  return `${String(Math.floor(safe / 60)).padStart(2, '0')}:${String(safe % 60).padStart(2, '0')}`;
}

export function laterTime(a: HHMM, b: HHMM): HHMM {
  return toMinutes(a) >= toMinutes(b) ? a : b;
}

export function addMinutes(time: HHMM, minutes: number, cap = 23 * 60 + 59): HHMM {
  return fromMinutes(Math.min(cap, toMinutes(time) + minutes));
}

export function formatWindow(start: HHMM, end: HHMM): string {
  return `${start}–${end}`;
}

export function formatDuration(minutes: number): string {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours === 0) return `${rest} мин`;
  return rest === 0 ? `${hours} ч` : `${hours} ч ${rest} мин`;
}

export function formatKm(km: number): string {
  return `${km.toFixed(1).replace('.', ',')} км`;
}

export function formatSigned(value: number, digits = 0): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return '0';
  const text = Math.abs(rounded).toFixed(digits).replace('.', ',');
  return rounded > 0 ? `+${text}` : `−${text}`;
}

export function shortAddress(address: string): string {
  return address.replace(/^(г\.\s*)?(Город\s+)?Москва,\s*/u, '').replace(/,\s*кв\.\s*\S+\s*$/u, '');
}
