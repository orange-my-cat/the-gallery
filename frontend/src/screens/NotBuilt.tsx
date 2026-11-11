/** Views the sidebar links to that later phases build (BRD, Phasing). */

import { Icon, type IconName } from '@/components/Icon'

export function NotBuilt({ icon, title, phase }: { readonly icon: IconName; readonly title: string; readonly phase: string }) {
  return (
    <div className="timeline">
      <div className="tl-stage">
        <div className="empty-state">
          <div>
            <div className="orb">
              <Icon name={icon} size={30} />
            </div>
            <div className="display">{title} isn’t built yet</div>
            <p>This view arrives in phase {phase} of the build. The timeline and folders work now.</p>
          </div>
        </div>
      </div>
    </div>
  )
}
