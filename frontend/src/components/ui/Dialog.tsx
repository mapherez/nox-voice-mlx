import { useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
type Props = {
  id: string;
  className: string;
  labelledBy: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  dismissOutsideX?: boolean;
  dialogRef?: RefObject<HTMLDialogElement | null>;
  tab?: string;
};
export function Dialog({
  id,
  className,
  labelledBy,
  open,
  onClose,
  children,
  dismissOutsideX,
  dialogRef,
  tab,
}: Props) {
  const ownRef = useRef<HTMLDialogElement>(null);
  const ref = dialogRef ?? ownRef;
  const returnFocus = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const dialog = ref.current!;
    if (open && !dialog.open) {
      returnFocus.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
      returnFocus.current?.focus();
    }
  }, [open, ref]);
  useLayoutEffect(
    () => () => {
      if (ref.current?.open) ref.current.close();
      returnFocus.current?.focus();
    },
    [ref],
  );
  useLayoutEffect(() => {
    const dialog = ref.current!;
    if (!dismissOutsideX) return;
    // Native cancel and the close button provide the keyboard equivalents.
    const dismiss = (event: MouseEvent) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right) onClose();
    };
    dialog.addEventListener("click", dismiss);
    return () => dialog.removeEventListener("click", dismiss);
  }, [dismissOutsideX, onClose, ref]);
  return (
    <dialog
      id={id}
      ref={ref}
      className={className}
      aria-labelledby={labelledBy}
      data-tab={tab}
      onClose={() => {
        if (!ref.current?.open && open) onClose();
      }}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}>
      {children}
    </dialog>
  );
}
