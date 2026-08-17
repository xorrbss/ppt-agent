"use client";

import { AuthoredVnextMasterClient } from "@/lib/authored-vnext/master/client";
import type { MasterRevisionEnvelope } from "@/lib/authored-vnext/master/contracts";

import MasterCatalogDialog from "./MasterCatalogDialog";

export interface MasterDesignerDialogProps {
  client?: AuthoredVnextMasterClient;
  /** The host changes its selection only after the catalog's explicit Apply. */
  onApplied: (published: MasterRevisionEnvelope) => void | Promise<void>;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  /** Restored as the pending visual selection whenever the dialog opens. */
  selectedMasterId?: string | null;
}

/**
 * Compatibility entry point for presentation hosts. The large dialog now
 * owns catalog -> designer -> catalog navigation; it never commits on close.
 */
export function MasterDesignerDialog({
  client,
  onApplied,
  onOpenChange,
  open,
  selectedMasterId,
}: MasterDesignerDialogProps) {
  return (
    <MasterCatalogDialog
      client={client}
      onApply={onApplied}
      onOpenChange={onOpenChange}
      open={open}
      selectedMasterId={selectedMasterId}
    />
  );
}

export default MasterDesignerDialog;
