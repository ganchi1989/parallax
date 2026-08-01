import { afterEach, describe, expect, it } from 'vitest';
import { createDesktopBridge, isTauriRuntime } from './bridge';

const originalTauriFlag = Object.getOwnPropertyDescriptor(globalThis, 'isTauri');

function setTauriFlag(value?: boolean) {
  if (value === undefined) {
    Reflect.deleteProperty(globalThis, 'isTauri');
    return;
  }
  Object.defineProperty(globalThis, 'isTauri', {
    configurable: true,
    value
  });
}

afterEach(() => {
  if (originalTauriFlag) {
    Object.defineProperty(globalThis, 'isTauri', originalTauriFlag);
  } else {
    Reflect.deleteProperty(globalThis, 'isTauri');
  }
});

describe('desktop bridge runtime selection', () => {
  it('uses the demo adapter in an ordinary browser', () => {
    setTauriFlag();

    expect(isTauriRuntime()).toBe(false);
    expect(createDesktopBridge().mode).toBe('browser-demo');
  });

  it('uses the native adapter when Tauri sets its supported runtime flag', () => {
    setTauriFlag(true);

    expect(isTauriRuntime()).toBe(true);
    expect(createDesktopBridge().mode).toBe('tauri');
  });

  it('previews the managed default project without creating it', async () => {
    setTauriFlag();
    const bridge = createDesktopBridge();
    const source = 'D:\\Films\\Echoes of Aster\\aster_canyon_prores.mov';

    await expect(bridge.defaultProjectBase()).resolves.toBe('D:\\Parallax Projects');
    const plan = await bridge.planNewProject(source);

    expect(plan).toMatchObject({
      sourcePath: source,
      sourceName: 'aster_canyon_prores.mov',
      baseDirectory: 'D:\\Parallax Projects',
      projectName: 'aster canyon prores',
      folderName: 'aster canyon prores',
      collisionIndex: 0,
      created: false
    });
    expect(plan.projectDirectory).toBe('D:\\Parallax Projects\\aster canyon prores');
  });

  it('allocates collision-safe demo folders and never overwrites the first project', async () => {
    setTauriFlag();
    const bridge = createDesktopBridge();
    const source = 'D:\\Footage\\Holiday.mp4';

    const first = await bridge.allocateNewProject(source);
    const nextPlan = await bridge.planNewProject(source);
    const second = await bridge.allocateNewProject(source);

    expect(first.created).toBe(true);
    expect(first.projectDirectory).toBe('D:\\Parallax Projects\\Holiday');
    expect(nextPlan.created).toBe(false);
    expect(nextPlan.collisionIndex).toBe(2);
    expect(nextPlan.folderName).toBe('Holiday (2)');
    expect(nextPlan.projectName).toBe('Holiday (2)');
    expect(nextPlan.projectDirectory).not.toBe(first.projectDirectory);
    expect(second.projectDirectory).toBe(nextPlan.projectDirectory);
  });

  it('keeps the optional base picker separate from opening an existing project', async () => {
    setTauriFlag();
    const bridge = createDesktopBridge();
    const source = 'D:\\Footage\\Interview.mp4';

    const customBase = await bridge.pickProjectBaseDirectory();
    expect(customBase).toBe('D:\\Films\\Stereo Projects');
    const customPlan = await bridge.planNewProject(source, customBase ?? undefined);
    expect(customPlan.projectDirectory).toBe('D:\\Films\\Stereo Projects\\Interview');

    await expect(bridge.pickProjectDirectory()).resolves.toBe('D:\\Films\\Echoes of Aster');
  });

  it('preserves complete Unicode characters at the 96 UTF-16-unit boundary', async () => {
    setTauriFlag();
    const bridge = createDesktopBridge();
    const expectedName = `${'a'.repeat(94)}😀`;
    const source = `D:\\Footage\\${expectedName}tail.mp4`;

    const plan = await bridge.planNewProject(source);

    expect(plan.projectName).toBe(expectedName);
    expect(plan.projectName.length).toBe(96);
    expect(plan.folderName).toBe(expectedName);
    expect(/[\ud800-\udbff]$/.test(plan.projectName)).toBe(false);
    expect(plan.projectDirectory).toBe(`D:\\Parallax Projects\\${expectedName}`);
  });

  it('reserves room for the Windows device-name disarming suffix', async () => {
    setTauriFlag();
    const bridge = createDesktopBridge();
    for (const source of [
      `D:\\Footage\\CON.${'x'.repeat(120)}.mp4`,
      'D:\\Footage\\COM¹.archive.mp4'
    ]) {
      const plan = await bridge.planNewProject(source);

      expect(plan.projectName).toBe(plan.folderName);
      expect(plan.projectName.endsWith(' Project')).toBe(true);
      expect(plan.projectName.length).toBeLessThanOrEqual(96);
      expect(/[\ud800-\udbff]$/.test(plan.projectName)).toBe(false);
      expect(/^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)/i.test(plan.projectName)).toBe(false);
    }
  });

  it('normalizes C1 control characters in demo project names', async () => {
    setTauriFlag();
    const bridge = createDesktopBridge();

    const plan = await bridge.planNewProject('D:\\Footage\\Scene\u0085One.mp4');

    expect(plan.projectName).toBe('Scene One');
  });
});
