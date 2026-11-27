import type {ReactElement} from 'react';
export type ProductFilePickerProps = {
  request?: (path: string, init?: RequestInit) => Promise<Response>;
  projectId?: string;
  accept?: string;
  disabled?: boolean;
  onChoose: (file: File) => Promise<unknown> | void;
  label?: string;
};
export function ProductFilePicker(props: ProductFilePickerProps): ReactElement;
