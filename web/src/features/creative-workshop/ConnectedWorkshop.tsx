import {useEffect} from 'react';
import type {Guard} from '@shared/ui/navigationGuards';
import {ResourceLibraryPage} from './ResourceLibraryPage';

export function ConnectedWorkshop({path, navigate}: {
  path: string;
  navigate: (path: string) => void;
  registerNavigationGuard: (guard: Guard | null) => void;
}) {
  const legacy = path.split('?')[0] === '/fpl-account';
  useEffect(() => {
    if (legacy) navigate('/creative-workshop?view=sources');
  }, [legacy, navigate]);
  return <ResourceLibraryPage view={legacy ? 'sources' : new URLSearchParams(path.split('?')[1]).get('view') ?? ''} />;
}
