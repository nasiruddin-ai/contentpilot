"use client";

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export type Option = { value: string; label: string };

type Props = {
  value: string | null | undefined;
  onChange: (value: string) => void;
  options: Option[];
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  size?: "sm" | "default";
};

/** A single-value select with readable labels. */
export function SimpleSelect({ value, onChange, options, placeholder = "Select…", className, disabled, size }: Props) {
  return (
    <Select
      value={value ?? null}
      onValueChange={(next) => {
        if (typeof next === "string") onChange(next);
      }}
      items={options}
      disabled={disabled}
    >
      <SelectTrigger className={className} size={size}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
