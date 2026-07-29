"use client";

import { useFormStatus } from "react-dom";
import { Button } from "react-aria-components";

export function ActionButton({ children, className = "primary-button" }: {
  children: React.ReactNode;
  className?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <Button className={className} type="submit" isDisabled={pending}>
      {pending ? "Working…" : children}
    </Button>
  );
}
