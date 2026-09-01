export type DirectorDirtyResolution = "save" | "discard" | "continue";

export interface DirectorDirtyRegistration {
  scope: string;
  label: string;
  save: () => Promise<boolean>;
  discard: () => void;
}

export class DirectorDirtyCoordinator {
  private registration?: DirectorDirtyRegistration;

  get active() {
    return this.registration;
  }

  register(registration?: DirectorDirtyRegistration) {
    this.registration = registration;
  }

  clear(scope?: string) {
    if (!scope || this.registration?.scope === scope) this.registration = undefined;
  }

  async resolve(
    choose: (registration: DirectorDirtyRegistration) => Promise<DirectorDirtyResolution>,
  ): Promise<boolean> {
    const registration = this.registration;
    if (!registration) return true;
    const resolution = await choose(registration);
    if (resolution === "continue") return false;
    if (resolution === "discard") {
      registration.discard();
      this.clear(registration.scope);
      return true;
    }
    const saved = await registration.save();
    if (saved) this.clear(registration.scope);
    return saved;
  }
}
