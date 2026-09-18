import { useEffect, useId, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { engineerColor } from '../lib/colors';
import type { BrigadeOption } from '../lib/events';
import { brigadeName } from '../lib/format';
import { engineerIdsOf } from '../lib/planView';
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
 * Где стоит список: под кнопкой, а у нижнего края окна над ней. Список привязан к окну, а не к карточке:
 * карточка заявки прокручивается, а окно выбора варианта обрезало бы его.
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

interface BrigadeListboxProps {
  /** Название списка для скринридера; с labelVisible это ещё и подпись слева от кнопки. */
  label: string;
  labelVisible?: boolean;
  /** Текст на кнопке. */
  text: string;
  /** Точка цвета бригады на кнопке. */
  dot?: boolean;
  /** Бригада, чьим цветом горит точка; null — заявка без бригады. */
  dotEngineerId?: string | null;
  options: BrigadeOption[];
  disabled?: boolean;
  /** Подсказка на кнопке: обычно почему выбирать нельзя. */
  title?: string;
  /** Фокус на кнопке сразу: карточка «отдать бригаде» заменяется посчитанной, и фокус не должен уходить со списка. */
  autoFocus?: boolean;
  /** Приписка справа от кнопки. */
  children?: ReactNode;
  onChoose(option: BrigadeOption): void;
  /** Список открылся или закрылся: окно выбора варианта отдаёт открытому списку Esc. */
  onOpenChange?(open: boolean): void;
}

/**
 * Список бригад по шаблону listbox: фокус на списке, активная строка через aria-activedescendant,
 * неподходящие бригады недоступны с причиной. Кнопку и строки рисует он же, а что делать с выбором,
 * решает хозяин списка: карточка заявки переназначает её, окно выбора варианта считает план с этой бригадой.
 */
export function BrigadeListbox({
  label,
  labelVisible = false,
  text,
  dot = false,
  dotEngineerId = null,
  options,
  disabled = false,
  title,
  autoFocus = false,
  children,
  onChoose,
  onOpenChange,
}: BrigadeListboxProps) {
  const state = useAppStore((s) => s.state);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [placement, setPlacement] = useState<CSSProperties>({});
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // Выбранная бригада погасила кнопку: фокус нужно вернуть на неё, когда выбирать снова можно.
  const focusBack = useRef(false);
  const id = useId();
  const labelId = `${id}-label`;
  const triggerId = `${id}-trigger`;
  const listId = `${id}-list`;
  const optionId = (index: number) => `${id}-option-${index}`;
  const listOpen = open && !disabled;
  const ids = state ? engineerIdsOf(state) : [];

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  };

  // Пока список открыт, выбирать стало нельзя: список закрывается.
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  // Выбор бригады гасит кнопку на время расчёта, и браузер уносит фокус с погасшей кнопки на <body>:
  // возвращаем его на кнопку, как только выбирать снова можно. Фокус, уведённый диспетчером, не трогаем.
  useEffect(() => {
    if (disabled) return;
    if (focusBack.current && (document.activeElement === null || document.activeElement === document.body)) {
      triggerRef.current?.focus();
    }
    focusBack.current = false;
  });

  // Хозяин списка знает, открыт ли он: закрытый вместе с карточкой список Esc больше не занимает.
  useEffect(() => {
    onOpenChange?.(listOpen);
    return () => onOpenChange?.(false);
  }, [listOpen, onOpenChange]);

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
      // Esc закрывает только список: панель «Почему», карточка заявки и окно выбора варианта остаются открытыми.
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    // Карточка прокручивается, окно меняет размер: список едет вместе с кнопкой.
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

  const openList = () => {
    const trigger = triggerRef.current;
    if (trigger) setPlacement(listPlacement(trigger.getBoundingClientRect(), window.innerWidth, window.innerHeight));
    // Список открывается на бригаде заявки, без неё — на первой подходящей.
    const currentIndex = options.findIndex((option) => option.current);
    setActive(currentIndex >= 0 ? currentIndex : nextEnabled(options, -1, 1));
    setOpen(true);
  };

  const choose = (option: BrigadeOption) => {
    if (option.disabled) return;
    close(true);
    focusBack.current = true;
    onChoose(option);
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
      {labelVisible && (
        <span className="brigade-picker__label" id={labelId}>
          {label}
        </span>
      )}
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        className="brigade-picker__trigger"
        aria-haspopup="listbox"
        aria-expanded={listOpen}
        aria-controls={listOpen ? listId : undefined}
        aria-labelledby={labelVisible ? `${labelId} ${triggerId}` : undefined}
        disabled={disabled}
        title={title}
        autoFocus={autoFocus}
        onClick={() => (open ? close(false) : openList())}
        onKeyDown={onTriggerKeyDown}
      >
        {dot && <span className="dot" style={{ background: engineerColor(dotEngineerId, ids) }} aria-hidden="true" />}
        <span className="brigade-picker__value">{text}</span>
        <span className="brigade-picker__caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {children}
      {listOpen && (
        <ul
          ref={listRef}
          id={listId}
          className="brigade-picker__list"
          role="listbox"
          aria-label={labelVisible ? undefined : label}
          aria-labelledby={labelVisible ? labelId : undefined}
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
