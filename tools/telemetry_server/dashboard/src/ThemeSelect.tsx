import { forwardRef, useId, type HTMLAttributes } from "react";
import { createPortal } from "react-dom";
import { Dropdown } from "react-bootstrap";
import { Check, ChevronDown } from "lucide-react";

// Menus escape the panels' rounded overflow clipping; Popper keeps them in view.
const PortalMenu = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement> & {
    show?: boolean;
    close?: unknown;
    align?: unknown;
  }
>(({ show: _show, close: _close, align: _align, ...props }, ref) =>
  createPortal(<div {...props} ref={ref} />, document.body),
);
PortalMenu.displayName = "PortalMenu";

export function ThemeSelect({
  label,
  value,
  options,
  onChange,
  id,
  className = "",
}: {
  label: string;
  value: string | number;
  options: { value: string | number; label: string }[];
  onChange: (value: string) => void;
  id?: string;
  className?: string;
}) {
  const generatedId = useId();
  const selected = options.find(
    (option) => String(option.value) === String(value),
  );
  return (
    <Dropdown
      className={`theme-select ${className}`}
      onSelect={(key) => {
        if (key !== null) onChange(key);
      }}
      focusFirstItemOnShow="keyboard"
    >
      <Dropdown.Toggle
        id={id || generatedId}
        className="form-select theme-select-trigger"
        variant="default"
        aria-label={`${label}：${selected?.label || value}`}
      >
        <span>{selected?.label || value}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </Dropdown.Toggle>
      <Dropdown.Menu
        as={PortalMenu}
        className="theme-select-menu"
        role="menu"
        aria-label={label}
      >
        {options.map((option) => (
          <Dropdown.Item
            as="button"
            type="button"
            key={option.value}
            eventKey={String(option.value)}
            role="menuitemradio"
            aria-checked={String(option.value) === String(value)}
            active={String(option.value) === String(value)}
          >
            <span>{option.label}</span>
            <Check
              size={14}
              className="theme-select-check"
              aria-hidden="true"
            />
          </Dropdown.Item>
        ))}
      </Dropdown.Menu>
    </Dropdown>
  );
}
