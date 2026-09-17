import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import type { ServiceRequest } from '../api/types';
import { engineerColor } from '../lib/colors';
import { brigadeOptions, reassignEvent, reassignState, type BrigadeOption } from '../lib/events';
import { brigadeName } from '../lib/format';
import { assignmentIndex, byId, engineerIdsOf } from '../lib/planView';
import { useAppStore } from '../store/useAppStore';

/** Самая большая высота списка бригад, px: около девяти строк, дальше список прокручивается. */
const LIST_MAX_HEIGHT = 320;
/** Самая маленькая ширина списка, px: имя бригады и причина, почему она не подходит, помещаются рядом. */
const LIST_MIN_WIDTH = 340;
/** Зазор между кнопкой и списком, px. */
const LIST_GAP = 4;
/** Отступ списка от краёв окна, px. */
const VIEWPORT_EDGE = 8;
/** Когда под кнопкой меньше места, список открывается над ней, если там просторнее, px. */
const MIN_ROOM_BELOW = 160;

/**
 * Где стоит список: под кнопкой, а у нижнего края окна над ней. Список привязан к окну, а не к карточке заявки:
 * карточка прокручивается и обрезала бы его.
 */
function listPlacement(trigger: DOMRect, viewportWidth: number, viewportHeight: number): CSSProperties {
  const width = Math.min(Math.max(trigger.width, LIST_MIN_WIDTH), viewportWidth - 2 * VIEWPORT_EDGE);
  const left = Math.max(VIEWPORT_EDGE, Math.min(trigger.left, viewportWidth - VIEWPORT_EDGE - width));
  const below = viewportHeight - trigger.bottom - LIST_GAP - VIEWPORT_EDGE;
  const above = trigger.top - LIST_GAP - VIEWPORT_EDGE;
  if (below < MIN_ROOM_BELOW && above > below) {
    return { left, width, bottom: viewportHeight - trigger.top + LIST_GAP, maxHeight: Math.min(LIST_MAX_HEIGHT, above) };
  }
  return { left, width, top: trigger.bottom + LIST_GAP, maxHeight: Math.min(LIST_MAX_HEIGHT, Math.max(below, MIN_ROOM_BELOW / 2)) };
}

/** Следующая доступная бригада от позиции в сторону step; нет такой — позиция остаётся. */
function nextEnabled(options: BrigadeOption[], from: number, step: 1 | -1): number {
  for (let index = from + step; index >= 0 && index < options.length; index += step) {
    if (!options[index].disabled) return index;
  }
  return from;
}

/**
 * Выбор бригады в карточке заявки: список всех бригад, бригада текущего плана выделена, неподходящие недоступны
 * с причиной. Выбор другой бригады ставит на время часов событие «Переназначение заявки», а сервер предлагает варианты.
 * Список по шаблону listbox: фокус на списке, активная строка через aria-activedescendant.
 */
export function BrigadePicker({ request }: { request: ServiceRequest }) {
  const state = useAppStore((s) => s.state);
  const busy = useAppStore((s) => s.busy);
  const showPrevious = useAppStore((s) => s.showPrevious);
  const clock = useAppStore((s) => s.clock);
  const applyEvent = useAppStore((s) => s.applyEvent);
  // Окно выбора варианта открывается и без расчёта, например на событии при проигрывании часов.
  const choosing = useAppStore((s) => s.choice !== null || s.choiceLoading);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [placement, setPlacement] = useState<CSSProperties>({});
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const id = useId();
  const labelId = `${id}-label`;
  const triggerId = `${id}-trigger`;
  const listId = `${id}-list`;
  const optionId = (index: number) => `${id}-option-${index}`;

  // Начатую работу проверяем по текущему плану, как у кнопок заявки: события меняют именно его.
  const visit = state ? assignmentIndex(state.plan).get(request.id)?.visit : undefined;
  const lock = state ? reassignState(request, visit, { busy, showPrevious, clock }) : { disabled: true, title: undefined };
  // Список лежит выше окна выбора варианта, а новое событие сбросило бы выбор: пока окно открыто, бригаду не выбирают.
  const locked = lock.disabled || choosing;
  const listOpen = open && !locked;
  const options = state ? brigadeOptions(request, state.engineers, state.plan) : [];

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  // Пока список открыт, началась работа, пошёл расчёт или открылось окно выбора: выбирать больше нельзя, список закрывается.
  useEffect(() => {
    if (locked) setOpen(false);
  }, [locked]);

  useEffect(() => {
    if (!open) return;
    listRef.current?.focus({ preventScroll: true });
    const place = (event?: Event) => {
      // Прокрутка самого списка его не сдвигает.
      if (event?.target instanceof Node && listRef.current?.contains(event.target)) return;
      const trigger = triggerRef.current;
      if (trigger) setPlacement(listPlacement(trigger.getBoundingClientRect(), window.innerWidth, window.innerHeight));
    };
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && (listRef.current?.contains(target) || triggerRef.current?.contains(target))) return;
      setOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      // Esc закрывает только список: панель «Почему» и карточка заявки остаются открытыми.
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    // Карточка заявки прокручивается, окно меняет размер: список едет вместе с кнопкой.
    document.addEventListener('scroll', place, true);
    window.addEventListener('resize', place);
    // Перехват раньше карты: она гасит нажатия, и клик по ней иначе не закрыл бы список.
    document.addEventListener('pointerdown', closeOutside, true);
    // Перехват раньше обычных слушателей документа, чтобы они увидели, что Esc уже занят.
    document.addEventListener('keydown', closeOnEscape, true);
    return () => {
      document.removeEventListener('scroll', place, true);
      window.removeEventListener('resize', place);
      document.removeEventListener('pointerdown', closeOutside, true);
      document.removeEventListener('keydown', closeOnEscape, true);
    };
  }, [open]);

  // Активная строка всегда видна в прокручиваемом списке.
  useEffect(() => {
    if (!open || active < 0) return;
    const option = listRef.current?.children[active] as HTMLElement | undefined;
    option?.scrollIntoView?.({ block: 'nearest' });
  }, [open, active]);

  if (!state) return null;

  const engineers = byId(state.engineers);
  const current = options.find((option) => option.current)?.engineer;
  const fixed = request.fixed_engineer_id ? engineers.get(request.fixed_engineer_id) : undefined;
  const ids = engineerIdsOf(state);
  // Выбор диспетчера держится и после событий; если бригада в плане уже другая, называем выбранную.
  const fixedNote = !request.fixed_engineer_id
    ? null
    : request.fixed_engineer_id === current?.id
      ? 'выбрана диспетчером'
      : `диспетчер выбрал: ${brigadeName(fixed?.name ?? request.fixed_engineer_id)}`;

  const openList = () => {
    const trigger = triggerRef.current;
    if (trigger) setPlacement(listPlacement(trigger.getBoundingClientRect(), window.innerWidth, window.innerHeight));
    // Список открывается на бригаде текущего плана, без неё — на первой подходящей.
    const currentIndex = options.findIndex((option) => option.current);
    setActive(currentIndex >= 0 ? currentIndex : nextEnabled(options, -1, 1));
    setOpen(true);
  };

  const choose = (option: BrigadeOption) => {
    if (option.disabled) return;
    close(true);
    if (option.current) return;
    void applyEvent(reassignEvent(request.id, option.engineer.id, clock));
  };

  const onTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (open || (event.key !== 'ArrowDown' && event.key !== 'ArrowUp')) return;
    event.preventDefault();
    openList();
  };

  const onListKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    switch (event.key) {
      case 'ArrowDown':
      case 'ArrowUp': {
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        setActive((index) => nextEnabled(options, index, step));
        break;
      }
      case 'Home': {
        event.preventDefault();
        const first = nextEnabled(options, -1, 1);
        if (first >= 0) setActive(first);
        break;
      }
      case 'End': {
        event.preventDefault();
        const last = nextEnabled(options, options.length, -1);
        if (last < options.length) setActive(last);
        break;
      }
      case 'Enter':
      case ' ':
        event.preventDefault();
        if (options[active]) choose(options[active]);
        break;
      case 'Tab':
        // Фокус уходит дальше по странице, список за ним не остаётся.
        setOpen(false);
        break;
    }
  };

  return (
    <div className="brigade-picker">
      <span className="brigade-picker__label" id={labelId}>
        Бригада:
      </span>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        className="brigade-picker__trigger"
        aria-haspopup="listbox"
        aria-expanded={listOpen}
        aria-controls={listOpen ? listId : undefined}
        aria-labelledby={`${labelId} ${triggerId}`}
        disabled={locked}
        title={lock.title}
        onClick={() => (open ? close(false) : openList())}
        onKeyDown={onTriggerKeyDown}
      >
        <span className="dot" style={{ background: engineerColor(current?.id, ids) }} aria-hidden="true" />
        <span className="brigade-picker__value">{current ? brigadeName(current.name) : 'Без бригады'}</span>
        <span className="brigade-picker__caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {fixedNote && <span className="muted brigade-picker__fixed">{fixedNote}</span>}
      {listOpen && (
        <ul
          ref={listRef}
          id={listId}
          className="brigade-picker__list"
          role="listbox"
          aria-labelledby={labelId}
          aria-activedescendant={active >= 0 ? optionId(active) : undefined}
          tabIndex={-1}
          style={placement}
          onKeyDown={onListKeyDown}
        >
          {options.map((option, index) => (
            <li
              key={option.engineer.id}
              id={optionId(index)}
              role="option"
              aria-selected={option.current}
              aria-disabled={option.disabled || undefined}
              className={[
                'brigade-picker__option',
                option.current ? 'brigade-picker__option--current' : '',
                index === active ? 'brigade-picker__option--active' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              onMouseMove={() => {
                if (!option.disabled && index !== active) setActive(index);
              }}
              onClick={() => choose(option)}
            >
              <span className="dot" style={{ background: engineerColor(option.engineer.id, ids) }} aria-hidden="true" />
              <span className="brigade-picker__name">{brigadeName(option.engineer.name)}</span>
              <span className="brigade-picker__note">{option.note}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
