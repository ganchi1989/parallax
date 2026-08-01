<script lang="ts">
  import { onMount } from 'svelte';
  import { Command, Keyboard, X } from 'lucide-svelte';
  import { appStore } from './lib/app-store';
  import { activeAnalysisStage, ANALYSIS_STAGES, deriveWorkspaceCapabilities } from './lib/analysis-state';
  import { desktopBridge } from './lib/bridge';
  import type { NewProjectPlan } from './lib/bridge';
  import type { AiRecommendation, AnalysisCoverage, AnalysisStageId, AnalysisTier, AppSettings, ExportOptions, PresetId, Project, RecentProject, Shot, StereoParameters, Toast, WorkerEvent, WorkspaceCapabilities, WorkspaceSession } from './lib/types';
  import { presetById } from './lib/constants';
  import { createId } from './lib/utils';
  import { draftAnalysisParams, estimateDepthParams, pipelineConfig, PRODUCTION_DEPTH_BACKEND, renderConfig } from './lib/worker-config';
  import {
    interpretAiResult,
    parseDraftAnalysisSnapshot,
    parseShotFeatures,
    ProjectSessionGuard,
    resolveForProject,
    toEngineShotFeatures
  } from './lib/project-session';
  import type { ProjectContext, ShotFeatureUpdate } from './lib/project-session';
  import TitleBar from './lib/components/TitleBar.svelte';
  import WelcomeView from './lib/components/WelcomeView.svelte';
  import ShotList from './lib/components/ShotList.svelte';
  import PreviewStage from './lib/components/PreviewStage.svelte';
  import DirectorPanel from './lib/components/DirectorPanel.svelte';
  import Timeline from './lib/components/Timeline.svelte';
  import ProcessingQueue from './lib/components/ProcessingQueue.svelte';
  import ToastStack from './lib/components/ToastStack.svelte';
  import SettingsModal from './lib/components/SettingsModal.svelte';
  import ExportModal from './lib/components/ExportModal.svelte';
  import AnalysisRail from './lib/components/AnalysisRail.svelte';
  import PreparingWorkspace from './lib/components/PreparingWorkspace.svelte';
  import NewProjectSetup from './lib/components/NewProjectSetup.svelte';

  let settingsOpen = false;
  let exportOpen = false;
  let shortcutsOpen = false;
  let shotsOpen = true;
  let toasts: Toast[] = [];
  let hasLlmKey = false;
  let keyBusy = false;
  let testState: 'idle'|'testing'|'success'|'error' = 'idle';
  let aiBusy = false;
  let recommendation: AiRecommendation | null = null;
  let aiNotice: string | null = null;
  let testRequestId: string | null = null;
  let aiRequest: {id:string;shotId:number;context:ProjectContext} | null = null;
  let openProjectRequest: {id:string;context:ProjectContext} | null = null;
  let summaryRefresh: {id:string;context:ProjectContext} | null = null;
  const cachedStageRecoveryTimers = new Map<string,ReturnType<typeof setTimeout>>();
  const cachedStageRecoveryRequests = new Map<string,{sourceId:string;stage:AnalysisStageId;context:ProjectContext}>();
  const recoveredImportIds = new Set<string>();
  let importContext: {sourcePath:string;projectDir:string;projectName:string;device:AppSettings['device'];createId:string;projectContext:ProjectContext;inspectId?:string;normalizeId?:string;shotsId?:string;draftId?:string;depthId?:string;featuresId?:string;directId?:string;media?:Record<string,unknown>;depthMode?:Project['depthMode'];productionFeatures?:ShotFeatureUpdate[]} | null = null;
  let newProjectSourcePath: string | null = null;
  let newProjectBaseDirectory: string | null = null;
  let newProjectDefaultBase: string | null = null;
  let newProjectUsesDefault = true;
  let newProjectPlan: NewProjectPlan | null = null;
  let projectSetupBusy = false;
  let projectSetupAllocating = false;
  let projectSetupError: string | null = null;
  let projectSetupRevision = 0;
  let previewUrls: Record<number,string> = {};
  let previewRenderingShots = new Set<number>();
  let originalAssetUrl: string | null = null;
  const projectSession = new ProjectSessionGuard();
  const previewJobs = new Map<string,{shotId:number;context:ProjectContext;autoplay:boolean}>();
  const dirtyShotIds = new Set<number>();
  let saveTimer: ReturnType<typeof setTimeout> | undefined;
  let revisionConflict = false;
  let productionFinalizing = false;
  let renderAllBusy = false;
  let pendingSave: {id:string;promise:Promise<boolean>;resolve:(saved:boolean)=>void;savedIds:Set<number>} | null = null;
  let lastFrame = 0;

  $: selectedShot = $appStore.project?.shots.find((shot)=>shot.id===$appStore.selectedShotId) ?? $appStore.project?.shots[0];
  $: selectionCount = $appStore.selectedShotIds.length || (selectedShot ? 1 : 0);
  $: workspaceCapabilities = currentCapabilities($appStore.workspace, $appStore.project);
  $: runningAnalysisStage = $appStore.workspace ? activeAnalysisStage($appStore.workspace) : null;
  $: workspacePreparing = Boolean(runningAnalysisStage);
  $: productionAnalysisActive = Boolean(runningAnalysisStage && ['depth','features','direct'].includes(runningAnalysisStage));
  $: productionStage = productionAnalysisActive && runningAnalysisStage ? $appStore.workspace?.stages[runningAnalysisStage] : undefined;
  $: productionStageLabel = runningAnalysisStage==='depth' ? 'Full-frame depth' : runningAnalysisStage==='features' ? 'Shot feature analysis' : runningAnalysisStage==='direct' ? 'Stereo Director' : undefined;
  $: canStartProductionAnalysis = Boolean($appStore.workspace?.artifacts.analysisTier==='sampled' && $appStore.workspace.stages.depth.status==='locked' && !productionFinalizing);
  $: selectedPreviewRendering = Boolean(selectedShot&&previewRenderingShots.has(selectedShot.id));
  $: previewedShotIds = Object.keys(previewUrls).map(Number);
  $: renderingShotIds = [...previewRenderingShots];
  $: if(productionFinalizing&&runningAnalysisStage!=='features'&&runningAnalysisStage!=='direct')productionFinalizing=false;

  function projectWorkerConfig(device:AppSettings['device']=$appStore.settings.device){return pipelineConfig(device,$appStore.settings.depthEngine)}

  /**
   * Takes the workspace and project as arguments so every `$:` consumer records
   * them as reactive dependencies. Reading them from the store inside the body
   * would leave the capability gates frozen at their initial locked values.
   */
  function currentCapabilities(workspace:WorkspaceSession|null,project:Project|null):WorkspaceCapabilities{
    if(workspace)return deriveWorkspaceCapabilities(workspace.artifacts);
    const demo=desktopBridge.mode==='browser-demo';
    const shotsReady=Boolean(project?.shots.length);
    const directionReady=demo||Boolean(project?.analysisTier!=='none'&&project?.analysisReady&&project.scriptRevision&&/^[a-f0-9]{64}$/.test(project.scriptRevision));
    const productionDirectionReady=demo||project?.analysisTier==='production';
    const enabled=(value:boolean,reason:string)=>value?{enabled:true}:{enabled:false,reason};
    return {
      sourceMonitor:enabled(Boolean(project),'Choose a source video first'),
      metadata:enabled(Boolean(project),'Media inspection has not finished'),
      shotNavigation:enabled(shotsReady,'Finding shot boundaries'),
      shotMetrics:enabled(directionReady,'Extracting dependable shot features'),
      directorEdit:enabled(directionReady,'Building the versioned Director script'),
      previewRender:enabled(directionReady&&productionDirectionReady,'Full-frame analysis is required for previews'),
      assistantAnalysis:enabled(directionReady,'Shot features and direction must finish first'),
      finalExport:enabled(directionReady&&productionDirectionReady&&(demo||project?.depthMode==='production'||project?.depthMode==='image-analysis'),'Run full-frame analysis to check final export readiness')
    };
  }

  function errorMessage(error:unknown,fallback:string):string{
    if(error instanceof Error&&error.message)return error.message;
    if(typeof error==='string'&&error.trim())return error;
    if(error&&typeof error==='object'&&'message' in error&&typeof error.message==='string')return error.message;
    return fallback;
  }

  function sourceDisplayName(path:string):string{
    const file=path.split(/[\\/]/).pop()?.replace(/\.[^.]+$/,'')||'Untitled project';
    return file.replace(/[_-]+/g,' ').replace(/\s+/g,' ').trim();
  }

  function exactShotIds(expected:readonly number[],actual:readonly number[]):boolean{
    if(!expected.length||expected.length!==actual.length)return false;
    const expectedSet=new Set(expected);const actualSet=new Set(actual);
    return expectedSet.size===expected.length&&actualSet.size===actual.length&&[...expectedSet].every((id)=>actualSet.has(id));
  }

  function importStageForId(id:string):AnalysisStageId|null{
    const context=importContext;
    if(!context||!projectSession.isCurrent(context.projectContext))return null;
    if(id===context.createId)return 'setup';
    if(id===context.inspectId)return 'inspect';
    if(id===context.normalizeId)return 'normalize';
    if(id===context.shotsId)return 'shots';
    if(id===context.draftId)return 'draft';
    if(id===context.depthId)return 'depth';
    if(id===context.featuresId)return 'features';
    if(id===context.directId)return 'direct';
    return null;
  }

  function activeImportRequestId():string|null{
    const context=importContext;const workspace=$appStore.workspace;
    if(!context||!workspace)return null;
    const stage=activeAnalysisStage(workspace);
    if(stage==='setup')return context.createId;
    if(stage==='inspect')return context.inspectId??null;
    if(stage==='normalize')return context.normalizeId??null;
    if(stage==='shots')return context.shotsId??null;
    if(stage==='draft')return context.draftId??null;
    if(stage==='depth')return context.depthId??null;
    if(stage==='features')return context.featuresId??null;
    if(stage==='direct')return context.directId??null;
    return null;
  }

  function failCurrentImport(stage:AnalysisStageId,message:string,cancelled=false){
    if(stage==='features'||stage==='direct')productionFinalizing=false;
    appStore.failWorkspaceStage(stage,message,cancelled);
    appStore.setBusy(false);
  }

  function toast(kind: Toast['kind'], title: string, message?: string) {
    const id = createId('toast');
    toasts = [...toasts, {id,kind,title,message}].slice(-4);
    window.setTimeout(()=>{toasts=toasts.filter((item)=>item.id!==id)}, kind==='error'?6500:4200);
  }

  function clearProjectAsyncState(){
    for(const timer of cachedStageRecoveryTimers.values())clearTimeout(timer);
    cachedStageRecoveryTimers.clear();cachedStageRecoveryRequests.clear();recoveredImportIds.clear();
    previewJobs.clear();previewUrls={};previewRenderingShots=new Set();originalAssetUrl=null;
    aiRequest=null;aiBusy=false;recommendation=null;aiNotice=null;
    summaryRefresh=null;openProjectRequest=null;importContext=null;
    productionFinalizing=false;
  }

  function beginProjectSession(projectPath:string):ProjectContext{
    clearProjectAsyncState();
    return projectSession.begin(projectPath);
  }

  function invalidateProjectSession(){
    projectSession.invalidate();
    clearProjectAsyncState();
  }

  function loadOriginalAsset(path:string,context:ProjectContext,title:string,message:string){
    void resolveForProject(
      projectSession,
      context,
      desktopBridge.assetUrl(path),
      (url)=>{originalAssetUrl=url||null},
      ()=>{originalAssetUrl=null;toast('info',title,message)}
    );
  }

  function clearNewProjectSetup(invalidate=true){
    if(invalidate)projectSetupRevision+=1;
    newProjectSourcePath=null;newProjectBaseDirectory=null;newProjectPlan=null;newProjectUsesDefault=true;
    projectSetupBusy=false;projectSetupAllocating=false;projectSetupError=null;
  }

  function cancelNewProjectSetup(){
    if(projectSetupAllocating)return;
    clearNewProjectSetup();appStore.setBusy(false);
  }

  async function importVideo(path?: string) {
    if($appStore.busy||projectSetupBusy||newProjectSourcePath)return;
    appStore.setBusy(true);
    try{
      const sourcePath=path||await desktopBridge.pickVideo();
      if(!sourcePath){appStore.setBusy(false);return}
      const revision=++projectSetupRevision;
      newProjectSourcePath=sourcePath;newProjectBaseDirectory=null;newProjectPlan=null;newProjectUsesDefault=true;projectSetupError=null;
      appStore.setBusy(false);projectSetupBusy=true;
      try{
        const defaultBase=await desktopBridge.defaultProjectBase();
        if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
        newProjectDefaultBase=defaultBase;newProjectBaseDirectory=defaultBase;
        const plan=await desktopBridge.planNewProject(sourcePath);
        if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
        newProjectPlan=plan;newProjectBaseDirectory=plan.baseDirectory;
      }catch(error){
        if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
        projectSetupError=errorMessage(error,'The default project destination could not be prepared. Choose another location.');
      }finally{
        if(revision===projectSetupRevision)projectSetupBusy=false;
      }
    }catch(error){
      appStore.setBusy(false);
      toast('error','Could not choose source video',errorMessage(error,'The native video picker did not respond.'));
    }
  }

  async function changeNewProjectBase(){
    const sourcePath=newProjectSourcePath;
    if(!sourcePath||projectSetupBusy||projectSetupAllocating)return;
    const revision=++projectSetupRevision;projectSetupBusy=true;
    try{
      const baseDirectory=await desktopBridge.pickProjectBaseDirectory();
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      if(!baseDirectory)return;
      projectSetupError=null;newProjectBaseDirectory=baseDirectory;newProjectPlan=null;newProjectUsesDefault=false;
      const plan=await desktopBridge.planNewProject(sourcePath,baseDirectory);
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      newProjectPlan=plan;newProjectBaseDirectory=plan.baseDirectory;
    }catch(error){
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      projectSetupError=errorMessage(error,'That location cannot hold a project. Choose another folder or use the default.');
    }finally{
      if(revision===projectSetupRevision)projectSetupBusy=false;
    }
  }

  async function useDefaultProjectBase(){
    const sourcePath=newProjectSourcePath;
    if(!sourcePath||projectSetupBusy||projectSetupAllocating)return;
    const revision=++projectSetupRevision;projectSetupBusy=true;projectSetupError=null;newProjectPlan=null;
    try{
      const defaultBase=newProjectDefaultBase??await desktopBridge.defaultProjectBase();
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      newProjectDefaultBase=defaultBase;newProjectBaseDirectory=defaultBase;newProjectUsesDefault=true;
      const plan=await desktopBridge.planNewProject(sourcePath);
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      newProjectPlan=plan;newProjectBaseDirectory=plan.baseDirectory;
    }catch(error){
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      projectSetupError=errorMessage(error,'The default project location is unavailable. Choose another folder.');
    }finally{
      if(revision===projectSetupRevision)projectSetupBusy=false;
    }
  }

  async function confirmNewProject(){
    const sourcePath=newProjectSourcePath;const planned=newProjectPlan;const usesDefault=newProjectUsesDefault;
    if(!sourcePath||!planned||$appStore.busy||projectSetupBusy||projectSetupAllocating||projectSetupError)return;
    const revision=++projectSetupRevision;projectSetupBusy=true;projectSetupAllocating=true;projectSetupError=null;appStore.setBusy(true);
    let projectContext:ProjectContext|null=null;
    try{
      const allocation=await desktopBridge.allocateNewProject(sourcePath,usesDefault?undefined:planned.baseDirectory);
      if(revision!==projectSetupRevision||newProjectSourcePath!==sourcePath)return;
      if(!allocation.created)throw new Error('The native host did not confirm that the project folder was created.');
      const finalSourcePath=allocation.sourcePath||sourcePath;
      const projectPath=allocation.projectDirectory;
      const projectName=allocation.projectName||sourceDisplayName(finalSourcePath);
      const allocationChanged=allocation.projectDirectory!==planned.projectDirectory;
      newProjectSourcePath=null;newProjectBaseDirectory=null;newProjectPlan=null;newProjectUsesDefault=true;projectSetupError=null;
      projectSetupBusy=false;projectSetupAllocating=false;
      projectContext=beginProjectSession(projectPath);
      if(desktopBridge.mode==='browser-demo'){
        const project=appStore.loadDemo(finalSourcePath,projectPath);
        appStore.setBusy(false);
        toast('success',allocationChanged?'Demo project name adjusted':'Demo project created',`${project.shots.length} deterministic demo shots are ready at ${projectPath}.`);
        return;
      }
      const id=createId('create-project');
      const device=$appStore.settings.device;importContext={sourcePath:finalSourcePath,projectDir:projectPath,projectName,device,createId:id,projectContext};
      appStore.openWorkspace({id:createId('workspace'),name:projectName,sourcePath:finalSourcePath,projectPath});
      appStore.setViewMode('original');
      loadOriginalAsset(finalSourcePath,projectContext,'Source monitor unavailable','The selected video is attached and analysis can continue, but the local monitor could not open it.');
      appStore.enqueue({id,title:'Creating project',detail:`Preparing ${allocation.folderName}`,stage:'Project setup',progress:.05,status:'processing'});
      await desktopBridge.request({id,method:'create_project',params:{input_path:finalSourcePath,project_dir:projectPath,name:projectName,...projectWorkerConfig(device)}});
      toast('info',allocationChanged?'Workspace created with an adjusted name':'Workstation opened',allocationChanged?`The planned name was taken, so your project was created at ${projectPath}.`:`Project files are organized at ${projectPath}. Background analysis is continuing.`);
    }catch(error){
      if(revision!==projectSetupRevision)return;
      if(projectContext&&!projectSession.isCurrent(projectContext))return;
      const message=errorMessage(error,projectContext?'The native worker did not respond.':'The project folder could not be created.');
      if(projectContext&&$appStore.workspace){failCurrentImport('setup',message);toast('error','Could not initialize project',message)}
      else{projectSetupError=message;appStore.setBusy(false)}
    }finally{
      if(revision===projectSetupRevision){projectSetupBusy=false;projectSetupAllocating=false}
    }
  }

  function record(value:unknown):Record<string,unknown>{return value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{}}

  function validatedMedia(value:unknown,label:string):Record<string,unknown>{
    const media=record(value);
    const width=Number(media.width);const height=Number(media.height);const fps=Number(media.frame_rate);const duration=Number(media.duration_seconds);
    if(typeof media.path!=='string'||!media.path||!Number.isFinite(width)||width<1||!Number.isFinite(height)||height<1||!Number.isFinite(fps)||fps<=0||!Number.isFinite(duration)||duration<=0){
      throw new Error(`${label} returned incomplete media metadata.`);
    }
    return media;
  }

  function artifactShotIds(rows:unknown[],label:string):number[]{
    const ids=rows.map((raw)=>Number(record(raw).shot_id));
    if(!ids.length||ids.some((id)=>!Number.isInteger(id)||id<1)||new Set(ids).size!==ids.length)throw new Error(`${label} returned invalid shot identifiers.`);
    return ids;
  }

  function hydrateDetectedProject(manifestValue:Record<string,unknown>){
    const context=importContext;
    if(!context||!projectSession.isCurrent(context.projectContext))throw new Error('Import context was lost.');
    const media=context.media??{};
    const rawShots=Array.isArray(manifestValue.shots)?manifestValue.shots:[];
    if(!rawShots.length)throw new Error('Shot detection returned an empty manifest.');
    artifactShotIds(rawShots,'Shot detection');
    const neutral=presetById('neutral');
    const shots:Shot[]=rawShots.map((raw,index)=>{
      const item=record(raw);const start=Number(item.start_time??0);const end=Number(item.end_time??start);
      if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start)throw new Error(`Shot ${index+1} has invalid timing.`);
      return {id:Number(item.shot_id??index+1),name:`Shot ${String(index+1).padStart(2,'0')}`,startSeconds:start,endSeconds:end,preset:'neutral',confidence:0,status:'analyzing',parameters:{...neutral.parameters},features:{durationSeconds:Math.max(0,end-start),motion:0,depthSpread:0,speech:0,foreground:0,brightness:0,cutFrequencyContext:0,cameraMovement:'static',depthReliability:0},color:index%2?'#788393':'#5bbdb2'};
    });
    const duration=Number(media.duration_seconds??shots.at(-1)?.endSeconds??0);
    const project:Project={id:createId('project'),name:context.projectName,sourcePath:context.sourcePath,projectPath:context.projectDir,durationSeconds:duration,width:Number(media.width??1280),height:Number(media.height??720),fps:Number(media.frame_rate??manifestValue.frame_rate??24),codec:String(media.video_codec??'unknown'),shots,updatedAt:new Date().toISOString(),dirty:false,analysisTier:'none',analysisReady:false,depthMode:context.depthMode??'unknown'};
    appStore.openProject(project);appStore.setViewMode('original');appStore.setBusy(false);
    if(desktopBridge.mode==='tauri'&&typeof media.path==='string')loadOriginalAsset(media.path,context.projectContext,'Source monitor proxy unavailable','The shot manifest is safe; render a shot preview after analysis to view output.');
    toast('success','Shot manifest ready',`${shots.length} real shots detected. Depth and direction analysis is continuing in the queue.`);
  }

  function importEventId(id:string):boolean{
    const context=importContext;return !!context&&projectSession.isCurrent(context.projectContext)&&[context.createId,context.inspectId,context.normalizeId,context.shotsId,context.draftId,context.depthId,context.featuresId,context.directId].includes(id);
  }

  function applyDirectedScript(resultValue:Record<string,unknown>,analysisTier:AnalysisTier='production',features?:ShotFeatureUpdate[],draftCoverage?:AnalysisCoverage):string{
    const script=record(resultValue.script??resultValue);const rawShots=Array.isArray(script.shots)?script.shots:[];
    const current=$appStore.project;if(!current)throw new Error('Project closed before analysis finished.');
    const expectedIds=current.shots.map((shot)=>shot.id);const actualIds=rawShots.map((raw)=>Number(record(raw).shot_id));
    if(!exactShotIds(expectedIds,actualIds))throw new Error('Director returned an incomplete or mismatched shot script.');
    const byId=new Map(rawShots.map((raw)=>{const item=record(raw);return [Number(item.shot_id),item] as const}));
    const featureById=new Map((features??[]).map((item)=>[item.shotId,item]));
    const shots=current.shots.map((shot)=>{const item=byId.get(shot.id);if(!item)return shot;const raw=record(item.parameters);const feature=featureById.get(shot.id);const parameters:StereoParameters={depthStrength:Number(raw.depth_strength??shot.parameters.depthStrength),convergence:Number(raw.convergence_depth_percentile??shot.parameters.convergence),backgroundDisparity:Number(raw.max_background_disparity_norm??shot.parameters.backgroundDisparity),popoutDisparity:Number(raw.max_popout_disparity_norm??shot.parameters.popoutDisparity),temporalSmoothing:Number(raw.temporal_smoothing??shot.parameters.temporalSmoothing),transitionFrames:Number(raw.transition_frames??shot.parameters.transitionFrames),edgeProtection:Boolean(raw.edge_protection??shot.parameters.edgeProtection)};const nextFeatures=feature?{durationSeconds:feature.durationSeconds,motion:feature.motion,depthSpread:feature.depthSpread,speech:feature.speech,foreground:feature.foreground,brightness:feature.brightness,cutFrequencyContext:feature.cutFrequencyContext,cameraMovement:feature.cameraMovement,depthReliability:feature.depthReliability}:shot.features;return {...shot,preset:String(item.preset??'neutral') as PresetId,confidence:Number(item.confidence??shot.confidence),status:'ready' as const,parameters,features:nextFeatures}});
    const revision=String(resultValue.revision??'');if(!/^[a-f0-9]{64}$/.test(revision))throw new Error('Director did not return a valid script revision.');
    dirtyShotIds.clear();revisionConflict=false;appStore.applyEngineState(shots,revision,true,analysisTier,draftCoverage);
    return revision;
  }

  /**
   * Classify depth by what actually produced each shot. A shot that fell back
   * to the image-analysis estimator still measured the picture, so it is not
   * the synthetic test pattern and must not be reported as one.
   */
  function depthModeFromShots(manifestBackend:string,rows:unknown[]):NonNullable<Project['depthMode']>{
    const backends=rows.map((raw)=>String(record(raw).backend??'').toLowerCase());
    if(manifestBackend.toLowerCase()==='synthetic'||backends.some((name)=>name.includes('synthetic')))return 'synthetic';
    if(!backends.length)return 'unknown';
    return backends.every((name)=>name===PRODUCTION_DEPTH_BACKEND)?'production':'image-analysis';
  }

  function summaryDepthMode(summary:Record<string,unknown>):NonNullable<Project['depthMode']>{
    if(summary.depth_status===null||summary.depth_status===undefined)return 'unknown';
    const status=record(summary.depth_status);
    // The engine reports how depth was actually measured. A fallback means the
    // configured backend name no longer describes the artifact on disk.
    if(status.tier==='certified')return 'production';
    if(status.tier==='image-analysis')return 'image-analysis';
    if(status.tier==='synthetic')return 'synthetic';
    if(status.production_ready===true)return 'production';
    const hasRows=(value:unknown)=>Array.isArray(value)&&value.length>0;
    if(String(status.backend).toLowerCase()==='synthetic'||hasRows(status.synthetic_shot_ids)||hasRows(status.fallback_shot_ids)||hasRows(status.model_failure_shot_ids))return 'synthetic';
    return 'unknown';
  }

  function applyRuntimeSummary(summary:Record<string,unknown>,context:ProjectContext){
    if(!projectSession.isCurrent(context))return;
    const mode=summaryDepthMode(summary);if(mode)appStore.setDepthMode(mode);
    const media=record(summary.media);
    if(desktopBridge.mode==='tauri'&&typeof media.path==='string'){
      loadOriginalAsset(media.path,context,'Normalized monitor unavailable','Project data is ready; render a shot preview to inspect stereo output.');
    }
  }

  function clearCachedStageRecovery(sourceId:string){
    const timer=cachedStageRecoveryTimers.get(sourceId);
    if(timer!==undefined)clearTimeout(timer);
    cachedStageRecoveryTimers.delete(sourceId);
  }

  function summaryStageComplete(summary:Record<string,unknown>,stage:string):boolean{
    const stages=record(record(summary.pipeline_state).stages);
    return record(stages[stage]).status==='complete';
  }

  /**
   * Repair a missed cached-result handoff only from complete, cross-validated
   * project artifacts. Worker progress alone is never allowed to unlock tools.
   */
  function reconcileCompletedSummary(summary:Record<string,unknown>,recovery:{sourceId:string;stage:AnalysisStageId;context:ProjectContext}):boolean{
    if(!projectSession.isCurrent(recovery.context))return false;
    const current=$appStore.project;const workspace=$appStore.workspace;
    if(!current||!workspace||workspace.stages.shots.status!=='complete'||workspace.stages[recovery.stage].status!=='running')return false;
    const shotManifest=record(summary.shots);const shotRows=Array.isArray(shotManifest.shots)?shotManifest.shots:[];
    const expectedIds=current.shots.map((shot)=>shot.id);const summaryShotIds=artifactShotIds(shotRows,'Cached project summary');
    if(!exactShotIds(expectedIds,summaryShotIds))throw new Error('Cached project summary does not match the open shot map.');

    const productionComplete=['estimate_depth','extract_features','direct'].every((stage)=>summaryStageComplete(summary,stage));
    if(productionComplete){
      const depthMode=summaryDepthMode(summary);if(depthMode==='unknown')return false;
      const featureManifest=record(summary.features);const featureRows=Array.isArray(featureManifest.shots)?featureManifest.shots:[];
      const features=parseShotFeatures(featureRows);const featureIds=features.map((item)=>item.shotId);
      const script=record(summary.stereo_script);const scriptRows=Array.isArray(script.shots)?script.shots:[];
      const scriptIds=scriptRows.map((raw)=>Number(record(raw).shot_id));const revision=String(summary.stereo_script_revision??'');
      if(!exactShotIds(expectedIds,featureIds)||!exactShotIds(expectedIds,scriptIds)||!/^[a-f0-9]{64}$/.test(revision))return false;
      const appliedRevision=applyDirectedScript({script,revision},'production',features);
      appStore.reconcileWorkspaceAnalysis('production',{depthMode,featureShotIds:featureIds,scriptRevision:appliedRevision,scriptShotIds:scriptIds});
      appStore.setDepthMode(depthMode);productionFinalizing=false;importContext=null;appStore.setBusy(false);
      appStore.applyWorkerEvent({type:'result',id:recovery.sourceId,result:{recovered_from_project:true}});
      recoveredImportIds.add(recovery.sourceId);applyRuntimeSummary(summary,recovery.context);
      toast('success','Completed analysis restored','Validated project artifacts were recovered automatically. Director and preview controls are ready.');
      return true;
    }

    if(recovery.stage==='draft'&&summaryStageComplete(summary,'analyze_draft')&&summary.draft_analysis!==null&&summary.draft_analysis!==undefined){
      const draft=record(summary.draft_analysis);const snapshot=parseDraftAnalysisSnapshot(draft,expectedIds);
      const revision=applyDirectedScript(draft,'sampled',snapshot.features,snapshot.coverage);
      appStore.reconcileWorkspaceAnalysis('sampled',{draftCoverage:snapshot.coverage,featureShotIds:expectedIds,scriptRevision:revision,scriptShotIds:expectedIds});
      appStore.setBusy(false);appStore.applyWorkerEvent({type:'result',id:recovery.sourceId,result:{recovered_from_project:true}});
      recoveredImportIds.add(recovery.sourceId);
      toast('success','Quick Director draft restored','Validated cached analysis was recovered automatically. You can edit immediately.');
      return true;
    }
    return false;
  }

  function scheduleCachedStageRecovery(sourceId:string,stage:AnalysisStageId){
    if(cachedStageRecoveryTimers.has(sourceId)||[...cachedStageRecoveryRequests.values()].some((item)=>item.sourceId===sourceId))return;
    const context=importContext?.projectContext;if(!context||!projectSession.isCurrent(context))return;
    const timer=setTimeout(()=>{
      cachedStageRecoveryTimers.delete(sourceId);
      if(!projectSession.isCurrent(context)||importStageForId(sourceId)!==stage||$appStore.workspace?.stages[stage].status!=='running')return;
      const id=createId('recover-project');cachedStageRecoveryRequests.set(id,{sourceId,stage,context});
      void desktopBridge.request({id,method:'get_project',params:{project_dir:context.projectPath}}).catch(()=>cachedStageRecoveryRequests.delete(id));
    },750);
    cachedStageRecoveryTimers.set(sourceId,timer);
  }

  async function continueImport(event:Extract<WorkerEvent,{type:'result'}>){
    const context=importContext;if(!context||!projectSession.isCurrent(context.projectContext))return;
    if(event.id===context.createId){
      appStore.completeWorkspaceStage('setup',{projectCreated:true});
      appStore.startWorkspaceStage('inspect','Reading timing, video, and audio metadata');
      const id=createId('inspect');context.inspectId=id;
      appStore.enqueue({id,title:'Inspecting source',detail:'Reading timing, video, and audio metadata',stage:'Media inspection',progress:.05,status:'processing'});
      await desktopBridge.request({id,method:'inspect',params:{project_dir:context.projectDir}});return;
    }
    if(event.id===context.inspectId){
      context.media=validatedMedia(event.result,'Media inspection');
      appStore.completeWorkspaceStage('inspect',{mediaReady:true});
      appStore.startWorkspaceStage('normalize','Creating a stable working copy while you stay in the workstation');
      const id=createId('normalize');context.normalizeId=id;
      appStore.enqueue({id,title:'Preparing working copy',detail:'Preserving source timing, frame order, and audio',stage:'Media normalization',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'normalize',params:{project_dir:context.projectDir}});return;
    }
    if(event.id===context.normalizeId){
      context.media=validatedMedia(event.result,'Media normalization');
      appStore.completeWorkspaceStage('normalize',{normalizedReady:true});
      if(typeof context.media.path==='string')loadOriginalAsset(context.media.path,context.projectContext,'Working-copy monitor unavailable','The normalized media is verified, but the monitor will keep using the selected source.');
      appStore.startWorkspaceStage('shots','Detecting hard cuts and building the shot map');
      const id=createId('detect-shots');context.shotsId=id;
      appStore.enqueue({id,title:'Detecting shots',detail:'Building a non-destructive shot manifest',stage:'Cut detection',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'detect_shots',params:{project_dir:context.projectDir}});return;
    }
    if(event.id===context.shotsId){
      const manifest=record(event.result);hydrateDetectedProject(manifest);
      const rows=Array.isArray(manifest.shots)?manifest.shots:[];const shotIds=artifactShotIds(rows,'Shot detection');
      appStore.completeWorkspaceStage('shots',{shotIds});
      appStore.startWorkspaceStage('draft','Sampling representative frames inside every shot');
      const id=createId('analyze-draft');context.draftId=id;
      appStore.enqueue({id,title:'Building quick Director draft',detail:'Sampling representative frames inside every shot',stage:'Quick draft',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'analyze_draft',params:draftAnalysisParams(context.projectDir,context.device,$appStore.settings.depthEngine)});return;
    }
    if(event.id===context.draftId){
      const result=record(event.result);const expectedIds=$appStore.workspace?.artifacts.shotIds??[];
      const snapshot=parseDraftAnalysisSnapshot(result,expectedIds);
      const revision=applyDirectedScript(result,'sampled',snapshot.features,snapshot.coverage);
      appStore.completeWorkspaceStage('draft',{analysisTier:'sampled',draftCoverage:snapshot.coverage,featureShotIds:expectedIds,scriptRevision:revision,scriptShotIds:expectedIds});
      appStore.setBusy(false);
      toast('success','Quick Director draft ready',`${snapshot.coverage.sampledFrames.toLocaleString()} representative frames analyzed across ${expectedIds.length} shots. You can edit now or run full-frame analysis when ready.`);return;
    }
    if(event.id===context.depthId){
      const depth=record(event.result);const depthRows=Array.isArray(depth.shots)?depth.shots:[];const depthIds=artifactShotIds(depthRows,'Depth analysis');const expectedIds=$appStore.workspace?.artifacts.shotIds??[];
      if(!exactShotIds(expectedIds,depthIds))throw new Error('Depth analysis did not cover every detected shot.');
      context.depthMode=depthModeFromShots(String(depth.backend),depthRows);appStore.setDepthMode(context.depthMode);
      appStore.completeWorkspaceStage('depth',{depthMode:context.depthMode});
      if(context.depthMode==='synthetic')toast('warning','Test depth fallback active','One or more shots use synthetic depth, which contains no scene geometry. Preview works, but export stays locked until real depth analysis succeeds.');
      else if(context.depthMode==='image-analysis')toast('info','Image-analysis depth in use','The neural depth model was unavailable, so depth was measured from the picture itself. Editing, preview, and export all work.');
      appStore.startWorkspaceStage('features','Measuring motion, speech, depth, and foreground context');
      const id=createId('extract-features');context.featuresId=id;
      appStore.enqueue({id,title:'Analyzing shots',detail:'Motion, speech, depth, and foreground features',stage:'Feature extraction',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'extract_features',params:{project_dir:context.projectDir,...projectWorkerConfig(context.device),allow_fallback:true}});return;
    }
    if(event.id===context.featuresId){
      const result=record(event.result);const rows=Array.isArray(result.shots)?result.shots:[];
      const parsed=parseShotFeatures(rows);const featureIds=parsed.map((item)=>item.shotId);const expectedIds=$appStore.workspace?.artifacts.shotIds??[];
      if(!exactShotIds(expectedIds,featureIds))throw new Error('Feature analysis did not cover every detected shot.');
      context.productionFeatures=parsed;productionFinalizing=true;
      if(!(await flushAllEdits())){productionFinalizing=false;throw new Error('Draft edits could not be secured before full-frame direction.');}
      appStore.completeWorkspaceStage('features',{featureShotIds:featureIds});
      appStore.startWorkspaceStage('direct','Selecting bounded presets and running Comfort Guard');
      const id=createId('direct');context.directId=id;
      appStore.enqueue({id,title:'Directing stereo',detail:'Selecting bounded presets and running Comfort Guard',stage:'Stereo Director',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'direct',params:{project_dir:context.projectDir,...projectWorkerConfig(context.device),allow_fallback:true,director:'rules'}});return;
    }
    if(event.id===context.directId){
      const projectDir=context.projectDir;const projectContext=context.projectContext;const features=context.productionFeatures;if(!features)throw new Error('Production features were lost before direction completed.');const revision=applyDirectedScript(record(event.result),'production',features);
      appStore.completeWorkspaceStage('direct',{analysisTier:'production',scriptRevision:revision,scriptShotIds:$appStore.project?.shots.map((shot)=>shot.id)??[]});importContext=null;productionFinalizing=false;appStore.setBusy(false);
      toast('success','Full-frame Director ready','Full-frame analysis is complete. Preview is ready; final export also requires certified depth.');
      const id=createId('open-project');summaryRefresh={id,context:projectContext};
      void desktopBridge.request({id,method:'get_project',params:{project_dir:projectDir}}).catch(()=>{if(summaryRefresh?.id===id&&projectSession.isCurrent(projectContext)){summaryRefresh=null;toast('info','Monitor refresh unavailable','Analysis is complete, but the normalized source monitor could not be refreshed.')}})
    }
  }

  function openDemo() {
    if(newProjectSourcePath)return;
    beginProjectSession('browser-demo://echoes-of-aster');
    appStore.loadDemo();
    toast('info','Demo workspace loaded','Every control is interactive; renders are simulated in the browser.');
  }

  async function openProject() {
    if(newProjectSourcePath)return;
    if(desktopBridge.mode==='browser-demo'){beginProjectSession('browser-demo://echoes-of-aster');appStore.loadDemo();toast('success','Demo project resumed','Cached demo depth and Director script are available.');return}
    let request:{id:string;context:ProjectContext}|null=null;
    try{const projectDir=await desktopBridge.pickProjectDirectory();if(!projectDir)return;const context=beginProjectSession(projectDir);const id=createId('open-project');request={id,context};openProjectRequest=request;appStore.setBusy(true);await desktopBridge.request({id,method:'get_project',params:{project_dir:projectDir}})}catch(error){if(request&&!projectSession.isCurrent(request.context))return;appStore.setBusy(false);if(openProjectRequest?.id===request?.id)openProjectRequest=null;toast('error','Could not open project',errorMessage(error,'The native host rejected the request.'))}
  }

  async function resumeExistingProject(summary:Record<string,unknown>,opening:{id:string;context:ProjectContext}){
    if(!projectSession.isCurrent(opening.context))return;
    const projectFile=record(summary.project);const projectDir=opening.context.projectPath;const sourcePath=String(projectFile.input_path??'').trim();
    if(!sourcePath)throw new Error('The project does not contain a valid source path.');
    const depthMode=summaryDepthMode(summary);const device=$appStore.settings.device;const projectName=String(projectFile.name??sourceDisplayName(sourcePath));
    appStore.openWorkspace({id:createId('workspace'),name:projectName,sourcePath,projectPath:projectDir});
    appStore.setViewMode('original');loadOriginalAsset(sourcePath,opening.context,'Source monitor unavailable','The project can continue, but its original source could not be opened in the monitor.');
    importContext={sourcePath,projectDir,projectName,device,createId:'open-existing',projectContext:opening.context,depthMode};
    appStore.completeWorkspaceStage('setup',{projectCreated:true});

    if(summary.media===null||summary.media===undefined){
      appStore.startWorkspaceStage('inspect','Continuing interrupted media inspection');
      const id=createId('inspect');importContext.inspectId=id;
      appStore.enqueue({id,title:'Resuming media inspection',detail:'Continuing an interrupted project import',stage:'Media inspection',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'inspect',params:{project_dir:projectDir}});return;
    }

    const media=validatedMedia(summary.media,'Cached working copy');importContext.media=media;
    appStore.startWorkspaceStage('inspect','Verified from the cached working copy');appStore.completeWorkspaceStage('inspect',{mediaReady:true});
    appStore.startWorkspaceStage('normalize','Verifying the cached working copy');appStore.completeWorkspaceStage('normalize',{normalizedReady:true});
    if(typeof media.path==='string')loadOriginalAsset(media.path,opening.context,'Working-copy monitor unavailable','Cached project artifacts are safe, but the local monitor could not open the working copy.');

    const shotManifest=record(summary.shots);const summarizedShots=Array.isArray(shotManifest.shots)?shotManifest.shots:[];
    if(!summarizedShots.length){
      appStore.startWorkspaceStage('shots','Rebuilding the missing shot manifest');
      const id=createId('detect-shots');importContext.shotsId=id;
      appStore.enqueue({id,title:'Resuming shot detection',detail:'Rebuilding the missing shot manifest from cached media',stage:'Cut detection',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'detect_shots',params:{project_dir:projectDir}});return;
    }

    const shotIds=artifactShotIds(summarizedShots,'Cached shot manifest');
    appStore.startWorkspaceStage('shots','Verifying cached shot boundaries');hydrateDetectedProject(shotManifest);appStore.completeWorkspaceStage('shots',{shotIds});
    applyRuntimeSummary(summary,opening.context);

    let restoredDraft=false;
    if(summary.draft_analysis!==null&&summary.draft_analysis!==undefined){
      try{
        const draft=record(summary.draft_analysis);const snapshot=parseDraftAnalysisSnapshot(draft,shotIds);const revision=applyDirectedScript(draft,'sampled',snapshot.features,snapshot.coverage);
        appStore.startWorkspaceStage('draft','Verified cached quick draft');appStore.completeWorkspaceStage('draft',{analysisTier:'sampled',draftCoverage:snapshot.coverage,featureShotIds:shotIds,scriptRevision:revision,scriptShotIds:shotIds});restoredDraft=true;
      }catch{/* Invalid draft artifacts never unlock controls; production artifacts may still supersede them. */}
    }
    if(!restoredDraft){
      appStore.startWorkspaceStage('draft',depthMode==='unknown'?'Rebuilding the quick Director draft':'Superseded by verified full-frame analysis');
      if(depthMode==='unknown'){
        const id=createId('analyze-draft');importContext.draftId=id;
        appStore.enqueue({id,title:'Rebuilding quick Director draft',detail:'Sampling representative frames inside every shot',stage:'Quick draft',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'analyze_draft',params:draftAnalysisParams(projectDir,device,$appStore.settings.depthEngine)});return;
      }
      appStore.completeWorkspaceStage('draft');
    }
    if(depthMode==='unknown'){
      appStore.setBusy(false);toast('success','Quick draft resumed','Sampled Director controls are ready. Run full-frame analysis when you want previews and an export-readiness check.');return;
    }
    appStore.startWorkspaceStage('depth','Verified cached full-frame depth');appStore.completeWorkspaceStage('depth',{depthMode});appStore.setDepthMode(depthMode);

    const featureManifest=record(summary.features);const featureRows=Array.isArray(featureManifest.shots)?featureManifest.shots:[];
    let parsedFeatures:ReturnType<typeof parseShotFeatures>=[];
    try{parsedFeatures=parseShotFeatures(featureRows)}catch{parsedFeatures=[]}
    const featureIds=parsedFeatures.map((item)=>item.shotId);
    if(!exactShotIds(shotIds,featureIds)){
      appStore.startWorkspaceStage('features','Completing dependable shot features');
      const id=createId('extract-features');importContext.featuresId=id;
      appStore.enqueue({id,title:'Resuming shot analysis',detail:'Completing missing motion, speech, and depth features',stage:'Feature extraction',progress:0,status:'processing'});
      await desktopBridge.request({id,method:'extract_features',params:{project_dir:projectDir,...projectWorkerConfig(device),allow_fallback:true}});return;
    }
    importContext.productionFeatures=parsedFeatures;appStore.startWorkspaceStage('features','Verified cached production features');

    const script=record(summary.stereo_script);const revision=String(summary.stereo_script_revision??'');const scriptRows=Array.isArray(script.shots)?script.shots:[];const scriptIds=scriptRows.map((raw)=>Number(record(raw).shot_id));
    const pipelineStages=record(record(summary.pipeline_state).stages);const cachedProductionDirectComplete=record(pipelineStages.direct).status==='complete';
    if(cachedProductionDirectComplete&&/^[a-f0-9]{64}$/.test(revision)&&exactShotIds(shotIds,scriptIds)){
      appStore.completeWorkspaceStage('features',{featureShotIds:featureIds});const appliedRevision=applyDirectedScript({script,revision},'production',parsedFeatures);appStore.startWorkspaceStage('direct','Verified cached production Director script');appStore.completeWorkspaceStage('direct',{analysisTier:'production',scriptRevision:appliedRevision,scriptShotIds:shotIds});
      importContext=null;appStore.setBusy(false);toast('success','Project resumed',depthMode==='production'?'Editing, preview, and production export are ready.':'Editing and preview are ready; production export still requires production depth.');return;
    }

    productionFinalizing=true;
    if(!(await flushAllEdits())){productionFinalizing=false;throw new Error('Draft edits could not be secured before full-frame direction.');}
    appStore.completeWorkspaceStage('features',{featureShotIds:featureIds});
    appStore.startWorkspaceStage('direct','Rebuilding the versioned comfort-safe script');
    const id=createId('direct');importContext.directId=id;
    appStore.enqueue({id,title:'Resuming Stereo Director',detail:'Building the versioned comfort-safe script',stage:'Stereo Director',progress:0,status:'processing'});
    await desktopBridge.request({id,method:'direct',params:{project_dir:projectDir,...projectWorkerConfig(device),allow_fallback:true,director:'rules'}});
  }

  async function openRecent(_recent: RecentProject) {await openProject()}

  async function startProductionAnalysis(){
    const workspace=$appStore.workspace;const context=importContext;
    if(!workspace||!context||!projectSession.isCurrent(context.projectContext)||workspace.artifacts.analysisTier!=='sampled')return;
    if(workspace.stages.depth.status!=='locked'){if(['failed','cancelled'].includes(workspace.stages.depth.status))await retryAnalysis();return}
    appStore.startWorkspaceStage('depth','Analyzing full-frame depth for production');
    const id=createId('estimate-depth');context.depthId=id;
    appStore.enqueue({id,title:'Full-frame depth analysis',detail:'Generating temporally stable depth for every frame',stage:'Full-frame depth',progress:0,status:'processing'});
    try{
      await desktopBridge.request({id,method:'estimate_depth',params:estimateDepthParams(context.projectDir,context.device,false,$appStore.settings.depthEngine)});
      toast('info','Full-frame analysis started','Your sampled Director draft remains editable while full-frame depth is analyzed locally.');
    }catch(error){
      const message=errorMessage(error,'Full-frame depth analysis could not start.');failCurrentImport('depth',message);toast('error','Full-frame analysis could not start',message);
    }
  }

  function selectedEditIds():number[]{return $appStore.selectedShotIds.length?$appStore.selectedShotIds:selectedShot?[selectedShot.id]:[]}

  function queueAutosave(){
    if(!$appStore.settings.autosave)return;
    if(saveTimer)clearTimeout(saveTimer);
    saveTimer=setTimeout(()=>{void flushEdits(false)},650);
  }

  function editPreset(preset:PresetId){
    if(productionFinalizing){toast('info','Applying full-frame analysis','Director controls will return as soon as the latest draft edits are carried into the full-frame script.');return}
    if(desktopBridge.mode==='tauri'&&!workspaceCapabilities.directorEdit.enabled){toast('warning','Direction is still analyzing',workspaceCapabilities.directorEdit.reason);return}
    selectedEditIds().forEach((id)=>dirtyShotIds.add(id));appStore.applyPreset(preset);if(!revisionConflict)queueAutosave();
  }

  function editParameter<K extends keyof StereoParameters>(key:K,value:StereoParameters[K]){
    if(productionFinalizing){toast('info','Applying full-frame analysis','Director controls will return as soon as the latest draft edits are carried into the full-frame script.');return}
    if(desktopBridge.mode==='tauri'&&!workspaceCapabilities.directorEdit.enabled){toast('warning','Direction is still analyzing',workspaceCapabilities.directorEdit.reason);return}
    selectedEditIds().forEach((id)=>dirtyShotIds.add(id));appStore.updateParameter(key,value);if(!revisionConflict)queueAutosave();
  }

  function flushEdits(notify=true):Promise<boolean>{
    if(saveTimer){clearTimeout(saveTimer);saveTimer=undefined}
    if(pendingSave)return pendingSave.promise;
    const project=$appStore.project;if(!project)return Promise.resolve(false);
    if(desktopBridge.mode==='browser-demo'){dirtyShotIds.clear();appStore.markSaved();if(notify)toast('success','Demo project saved','Browser demo state was persisted locally.');return Promise.resolve(true)}
    if(!dirtyShotIds.size){if(notify)toast('info','Project is up to date','No Director changes need saving.');return Promise.resolve(true)}
    if(!project.scriptRevision||!/^[a-f0-9]{64}$/.test(project.scriptRevision)){toast('warning','Save is waiting for analysis','A versioned Director script is required before edits can be committed.');return Promise.resolve(false)}
    if(revisionConflict){toast('warning','Resolve the project conflict first','Your local edits are preserved. Reopen the project to load the disk version, then reapply or compare your changes.');return Promise.resolve(false)}
    const savedIds=new Set(dirtyShotIds);savedIds.forEach((shotId)=>dirtyShotIds.delete(shotId));
    const overrides=project.shots.filter((shot)=>savedIds.has(shot.id)).map((shot)=>({shot_id:shot.id,preset:shot.preset,parameters:{depth_strength:shot.parameters.depthStrength,convergence_depth_percentile:shot.parameters.convergence,max_background_disparity_norm:shot.parameters.backgroundDisparity,max_popout_disparity_norm:shot.parameters.popoutDisparity,temporal_smoothing:shot.parameters.temporalSmoothing,transition_frames:shot.parameters.transitionFrames,edge_protection:shot.parameters.edgeProtection}}));
    const id=createId('save-script');let resolveSave!:(saved:boolean)=>void;const promise=new Promise<boolean>((resolve)=>{resolveSave=resolve});pendingSave={id,promise,resolve:resolveSave,savedIds};
    void desktopBridge.request({id,method:'apply_shot_overrides',params:{project_dir:project.projectPath,expected_revision:project.scriptRevision,overrides}}).catch((error)=>{const active=pendingSave;if(active?.id===id){active.savedIds.forEach((shotId)=>dirtyShotIds.add(shotId));active.resolve(false);pendingSave=null}toast('error','Director changes were not saved',errorMessage(error,'The native host rejected the request.'))});
    return promise;
  }

  async function saveProject() {
    if(productionFinalizing){toast('info','Applying full-frame analysis','Draft edits are already secured. Save will return when the full-frame Director script is ready.');return}
    if(desktopBridge.mode==='tauri'&&!workspaceCapabilities.directorEdit.enabled){toast('warning','Save is still locked',workspaceCapabilities.directorEdit.reason);return}
    const saved=await flushEdits(false);if(saved)toast('success','Project saved','Versioned shot overrides were committed atomically.');
  }

  async function flushAllEdits():Promise<boolean>{
    for(let attempt=0;attempt<3;attempt+=1){
      if(!pendingSave&&!dirtyShotIds.size)return true;
      if(!(await flushEdits(false)))return false;
    }
    return !pendingSave&&!dirtyShotIds.size;
  }

  function applyHistory(direction:'undo'|'redo'){
    if(productionFinalizing)return;
    if(desktopBridge.mode==='tauri'&&!workspaceCapabilities.directorEdit.enabled)return;
    const changed=direction==='undo'?appStore.undo():appStore.redo();if(!changed)return;
    $appStore.project?.shots.forEach((shot)=>dirtyShotIds.add(shot.id));if(!revisionConflict)queueAutosave();
  }

  async function closeProjectSafely(){
    const project=$appStore.project;if(!project&&!$appStore.workspace)return;
    if(project&&(project.dirty||dirtyShotIds.size||pendingSave)){
      const saved=await flushAllEdits();
      if(!saved&&!window.confirm('This project has local changes that could not be saved. Discard them and return home?'))return;
    }
    const runningImportId=activeImportRequestId();if(runningImportId&&desktopBridge.mode==='tauri'){try{await desktopBridge.cancelJob(runningImportId)}catch{/* Session invalidation below safely ignores a late result. */}}
    invalidateProjectSession();revisionConflict=false;dirtyShotIds.clear();appStore.closeProject();
  }

  /** Frame the playhead sits on, measured from the start of the shot. */
  function previewFrameOffset(shot:Shot,project:Project):number{
    const fps=Number.isFinite(project.fps)&&project.fps>0?project.fps:24;
    const elapsed=Math.max(0,$appStore.currentTime-shot.startSeconds);
    const lastFrameInShot=Math.max(0,Math.round((shot.endSeconds-shot.startSeconds)*fps)-1);
    return Math.min(Math.round(elapsed*fps),lastFrameInShot);
  }

  /**
   * A still renders the playhead frame in about a second, so it is the default
   * while a director adjusts depth. Video renders the whole shot and is opt-in.
   */
  /**
   * Pressing play in Anaglyph view when only a still exists renders the clip, so
   * "play" means play rather than "nothing happens until you find a button".
   */
  function togglePlayback(){
    const shot=selectedShot;
    const showingStill=Boolean(shot&&previewUrls[shot.id]?.match(/\.png(\?|$)/i));
    const wantsClip=!$appStore.playing&&$appStore.viewMode==='anaglyph'&&desktopBridge.mode==='tauri'
      &&shot&&!previewRenderingShots.has(shot.id)&&workspaceCapabilities.previewRender.enabled
      &&(showingStill||!previewUrls[shot.id]);
    if(wantsClip){void renderPreview('video',true);return}
    appStore.togglePlayback();
  }

  /**
   * Render every shot that has no clip yet, one after another. The engine runs
   * a single job at a time — it renders frames across cores internally — so
   * queueing them here keeps the worker busy without racing its stage state.
   */
  async function renderAllShots(){
    const project=$appStore.project;const context=projectSession.capture();
    if(!project||!context)return;
    if(!workspaceCapabilities.previewRender.enabled){toast('warning','Preview is not ready',workspaceCapabilities.previewRender.reason);return}
    if(renderAllBusy)return;
    const queue=project.shots.filter((shot)=>!previewUrls[shot.id]&&!previewRenderingShots.has(shot.id));
    if(!queue.length){toast('info','Every shot is rendered','All shots already have a 3D clip.');return}
    renderAllBusy=true;
    toast('info','Rendering every shot',`${queue.length} shot${queue.length===1?'':'s'} queued. You can keep working; each finishes in order.`);
    try{
      for(const shot of queue){
        if(!projectSession.isCurrent(context)||!renderAllBusy)break;
        await renderPreview('video',false,shot);
        // Wait for this shot before starting the next, so the queue stays honest.
        while(projectSession.isCurrent(context)&&previewRenderingShots.has(shot.id)){
          await new Promise((resolve)=>setTimeout(resolve,250));
        }
      }
      if(projectSession.isCurrent(context))toast('success','Batch render finished','Every queued shot has a 3D clip.');
    }finally{
      renderAllBusy=false;
    }
  }

  async function renderPreview(kind:'still'|'video'='still',autoplay=false,target?:Shot) {
    const shot=target??selectedShot;const project=$appStore.project;const context=projectSession.capture();
    if (!shot || !project || !context) return;
    if(!workspaceCapabilities.previewRender.enabled){toast('warning','Preview is not ready',workspaceCapabilities.previewRender.reason);return}
    if(previewRenderingShots.has(shot.id))return;
    previewRenderingShots=new Set(previewRenderingShots).add(shot.id);
    if(!(await flushEdits(false))){const next=new Set(previewRenderingShots);next.delete(shot.id);previewRenderingShots=next;return}
    if(!projectSession.isCurrent(context))return;
    const video=kind==='video';
    const id=createId(video?'preview':'preview-frame');
    previewJobs.set(id,{shotId:shot.id,context,autoplay:autoplay&&kind==='video'});
    appStore.enqueue({id,title:`Shot ${String(shot.id).padStart(2,'0')} ${video?'3D clip':'preview'}`,detail:shot.name,stage:'Queued',progress:0,status:'processing'});
    try{
      await desktopBridge.request({id,method:video?'render_preview':'render_preview_frame',params:{
        project_dir:project.projectPath,...projectWorkerConfig(),...renderConfig($appStore.settings),allow_fallback:true,shot_id:shot.id,
        ...(video?{}:{frame_offset:previewFrameOffset(shot,project)})
      }});
      if(video&&projectSession.isCurrent(context))toast('info','Rendering the shot in 3D','Every frame of this shot is being rendered locally. The viewer switches to playback when it finishes.');
    }catch(error){
      previewJobs.delete(id);
      const next=new Set(previewRenderingShots);next.delete(shot.id);previewRenderingShots=next;
      if(!projectSession.isCurrent(context))return;
      appStore.applyWorkerEvent({type:'error',id,error:{code:'preview_request_failed',message:errorMessage(error,'Preview request failed'),retryable:true}});
      toast('error','Preview could not start',errorMessage(error,'The native host rejected the request. Restart the app if it was updated while running.'));
    }
  }

  async function startExport(options: ExportOptions) {
    if (!$appStore.project) return;
    if(!workspaceCapabilities.finalExport.enabled){toast('error','Production export is locked',workspaceCapabilities.finalExport.reason);return}
    const depthTier=$appStore.workspace?.artifacts.depthMode??$appStore.project.depthMode;
    if(depthTier==='synthetic'){toast('error','Export is locked','Synthetic test depth carries no scene geometry. Run depth analysis with the neural model or the built-in image-analysis engine first.');return}
    if(!$appStore.project.analysisReady||!$appStore.project.scriptRevision){toast('error','Director analysis is incomplete','Wait for the versioned stereo script before starting a final render.');return}
    if(!(await flushEdits(false)))return;
    exportOpen=false;
    const id=createId('export');
    appStore.enqueue({id,title:`${$appStore.project.name} master`,detail:options.outputPath,stage:'Queued',progress:0,status:'processing'});
    try{
      const outputMode=options.format==='side_by_side'?'side-by-side':'anaglyph';
      await desktopBridge.request({id,method:'render_final',params:{project_dir:$appStore.project.projectPath,...projectWorkerConfig(),output_path:options.outputPath,output_mode:outputMode,anaglyph_mode:options.anaglyphMode,swap_eyes:options.swapEyes}});
      toast('success','Export added to queue','Processing continues in the background and can be resumed.');
    }catch(error){
      appStore.applyWorkerEvent({type:'error',id,error:{code:'export_request_failed',message:error instanceof Error?error.message:'Export request failed',retryable:true}});
      toast('error','Export could not start',errorMessage(error,'The native host rejected the request.'));
    }
  }

  async function cancelJob(id:string){
    const stage=importStageForId(id);
    try{await desktopBridge.cancelJob(id);if(stage)failCurrentImport(stage,'Analysis was paused by you.',true);appStore.removeQueueItem(id);toast('warning','Job cancelled','Completed stage caches were kept.')}catch(error){toast('error','Could not cancel job',errorMessage(error,'The native host rejected the request.'))}
  }

  async function retryAnalysis(){
    const workspace=$appStore.workspace;const context=importContext;
    if(!workspace||!context||!projectSession.isCurrent(context.projectContext))return;
    const definition=ANALYSIS_STAGES.find((item)=>['failed','cancelled'].includes(workspace.stages[item.id].status));
    if(!definition)return;
    const stage=definition.id;appStore.startWorkspaceStage(stage,`Retrying ${definition.description.toLowerCase()}`);
    let id='';
    try{
      if(stage==='setup'){
        id=createId('create-project');context.createId=id;
        appStore.enqueue({id,title:'Retrying project setup',detail:'Validating source and project folder',stage:'Project setup',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'create_project',params:{input_path:context.sourcePath,project_dir:context.projectDir,name:context.projectName,...projectWorkerConfig(context.device)}});
      }else if(stage==='inspect'){
        id=createId('inspect');context.inspectId=id;
        appStore.enqueue({id,title:'Retrying media inspection',detail:'Reading timing, video, and audio metadata',stage:'Media inspection',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'inspect',params:{project_dir:context.projectDir,force_stages:['inspect']}});
      }else if(stage==='normalize'){
        id=createId('normalize');context.normalizeId=id;
        appStore.enqueue({id,title:'Retrying working copy',detail:'Preserving source timing, frame order, and audio',stage:'Media normalization',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'normalize',params:{project_dir:context.projectDir,force_stages:['normalize']}});
      }else if(stage==='shots'){
        id=createId('detect-shots');context.shotsId=id;
        appStore.enqueue({id,title:'Retrying shot detection',detail:'Rebuilding the verified shot manifest',stage:'Cut detection',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'detect_shots',params:{project_dir:context.projectDir,force_stages:['detect_shots']}});
      }else if(stage==='draft'){
        id=createId('analyze-draft');context.draftId=id;
        appStore.enqueue({id,title:'Retrying quick Director draft',detail:'Sampling representative frames inside every shot',stage:'Quick draft',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'analyze_draft',params:draftAnalysisParams(context.projectDir,context.device,$appStore.settings.depthEngine)});
      }else if(stage==='depth'){
        id=createId('estimate-depth');context.depthId=id;
        appStore.enqueue({id,title:'Retrying depth analysis',detail:'Generating stable depth for every shot',stage:'Depth inference',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'estimate_depth',params:estimateDepthParams(context.projectDir,context.device,true,$appStore.settings.depthEngine)});
      }else if(stage==='features'){
        id=createId('extract-features');context.featuresId=id;
        appStore.enqueue({id,title:'Retrying shot analysis',detail:'Rebuilding dependable feature coverage',stage:'Feature extraction',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'extract_features',params:{project_dir:context.projectDir,...projectWorkerConfig(context.device),allow_fallback:true,force_stages:['extract_features']}});
      }else{
        productionFinalizing=true;
        if(!(await flushAllEdits())){productionFinalizing=false;throw new Error('Draft edits could not be secured before full-frame direction.');}
        id=createId('direct');context.directId=id;
        appStore.enqueue({id,title:'Retrying Stereo Director',detail:'Rebuilding the versioned comfort-safe script',stage:'Stereo Director',progress:0,status:'processing'});
        await desktopBridge.request({id,method:'direct',params:{project_dir:context.projectDir,...projectWorkerConfig(context.device),allow_fallback:true,director:'rules',force_stages:['direct']}});
      }
      toast('info','Analysis retry started',`${definition.label} is running again; completed upstream work was kept.`);
    }catch(error){const message=errorMessage(error,'The worker could not restart this stage.');failCurrentImport(stage,message);toast('error','Retry could not start',message)}
  }

  async function openSettings(){
    if(newProjectSourcePath)return;
    settingsOpen=true;
    try{hasLlmKey=await desktopBridge.hasLlmKey()}catch{hasLlmKey=false}
  }

  async function saveLlmKey(key:string){
    if(desktopBridge.mode!=='tauri'){toast('warning','Desktop app required','The browser demo never accepts API keys.');return}
    keyBusy=true;
    try{const status=await desktopBridge.saveLlmKey(key);if(status.workerRestartRequired){await desktopBridge.restartWorker()}hasLlmKey=status.configured;appStore.updateSettings({llmEnabled:status.configured});toast('success','API key secured',status.workerRestartRequired?'Credential stored and the local worker restarted safely.':'Stored by the native credential manager; it was not added to project data.')}catch(error){toast('error','Could not activate API key',errorMessage(error,'The credential manager rejected the request.'))}finally{keyBusy=false}
  }

  async function deleteLlmKey(){
    keyBusy=true;
    try{const status=await desktopBridge.deleteLlmKey();hasLlmKey=status.configured;appStore.updateSettings({llmEnabled:status.configured&&$appStore.settings.llmEnabled});if(status.configured&&status.source==='environment'){toast('warning','Credential-store key removed','An environment-provided key is still active. Restart the app without that environment variable to disable the assistant.')}else{toast('success','API key removed','The local credential was deleted.')}}catch(error){toast('error','Could not delete API key',errorMessage(error,'The credential manager rejected the request.'))}finally{keyBusy=false}
  }

  async function testLlm(){
    if(!hasLlmKey)return;
    testState='testing';testRequestId=createId('llm-test');
    try{await desktopBridge.request({id:testRequestId,method:'test_llm',params:{}})}catch(error){testState='error';toast('error','Connection test failed',errorMessage(error,'The native host rejected the request.'))}
  }

  async function askAi(){
    const shot=selectedShot;const project=$appStore.project;const context=projectSession.capture();
    if(!shot||!project||!context||!$appStore.settings.llmEnabled||!hasLlmKey)return;
    if(!workspaceCapabilities.assistantAnalysis.enabled){toast('warning','Shot analysis is incomplete',workspaceCapabilities.assistantAnalysis.reason);return}
    const id=createId('recommend');aiBusy=true;aiNotice=null;recommendation=null;aiRequest={id,shotId:shot.id,context};
    const canonicalFeatures=toEngineShotFeatures(shot.id,shot.features);
    try{await desktopBridge.request({id,method:'recommend_preset',params:{features:canonicalFeatures}})}catch(error){if(aiRequest?.id===id)aiRequest=null;if(!projectSession.isCurrent(context))return;aiBusy=false;toast('error','Assistant unavailable',errorMessage(error,'The native host rejected the request.'))}
  }

  function handleWorkerEvent(event:WorkerEvent){
    if(event.type==='result'||event.type==='error'){
      clearCachedStageRecovery(event.id);
      if(recoveredImportIds.delete(event.id))return;
    }
    appStore.applyWorkerEvent(event);
    if(event.type==='progress'){
      const stage=importStageForId(event.job_id);if(stage){const progress=event.total>0?event.completed/event.total:0;appStore.updateWorkspaceProgress(stage,progress,event.message||event.stage,event.completed,event.total);if(event.total>0&&event.completed>=event.total&&/using cached stage output/i.test(event.message??''))scheduleCachedStageRecovery(event.job_id,stage)}
    }
    if(event.type==='result'){
      const recovery=cachedStageRecoveryRequests.get(event.id);
      if(recovery){
        cachedStageRecoveryRequests.delete(event.id);
        try{reconcileCompletedSummary(record(event.result),recovery)}catch(error){toast('warning','Cached analysis could not be restored',errorMessage(error,'The completed artifacts could not be validated.'))}
        return;
      }
      if(event.id===pendingSave?.id){const active=pendingSave;const revision=String(event.result.revision??'');if(/^[a-f0-9]{64}$/.test(revision)){appStore.setScriptRevision(revision,dirtyShotIds.size>0);active.resolve(true)}else{active.savedIds.forEach((shotId)=>dirtyShotIds.add(shotId));active.resolve(false);toast('error','Save response was invalid','The engine did not return a valid script revision.')}pendingSave=null;if(dirtyShotIds.size)queueAutosave();return}
      const refresh=summaryRefresh;
      if(event.id===refresh?.id){summaryRefresh=null;if(projectSession.isCurrent(refresh.context))applyRuntimeSummary(record(event.result),refresh.context);return}
      const opening=openProjectRequest;
      if(event.id===opening?.id){
        openProjectRequest=null;
        if(!projectSession.isCurrent(opening.context))return;
        void resumeExistingProject(record(event.result),opening).catch((error)=>{
          if(!projectSession.isCurrent(opening.context))return;
          const stage=$appStore.workspace?activeAnalysisStage($appStore.workspace):null;
          const message=errorMessage(error,'Project resume could not continue.');if(stage)failCurrentImport(stage,message);else appStore.setBusy(false);
          toast('error','Project could not be resumed',message);
        });return;
      }
      if(importEventId(event.id)){const context=importContext!.projectContext;void continueImport(event).catch((error)=>{if(!projectSession.isCurrent(context))return;const stage=$appStore.workspace?activeAnalysisStage($appStore.workspace):null;const message=errorMessage(error,'Import could not continue.');if(stage)failCurrentImport(stage,message);else appStore.setBusy(false);toast('error','Import could not continue',message)});return}
      if(event.id===testRequestId){testState=event.result.connected===true?'success':'error';testRequestId=null;if(testState==='success')toast('success','OpenAI connected',`Model ${String(event.result.model??$appStore.settings.llmModel)} is available.`);else toast('error','Connection test failed',String(event.result.error_code??'Provider did not confirm a connection.'))}
      const assistantRequest=aiRequest;
      if(event.id===assistantRequest?.id){
        aiRequest=null;
        if(!projectSession.isCurrent(assistantRequest.context))return;
        const outcome=interpretAiResult(event.result,assistantRequest.shotId);
        if(outcome.kind==='fallback'){aiNotice=outcome.notice;recommendation=null;toast('warning',outcome.source==='assistant_low_confidence'?'Neutral safety fallback':'Local fallback used',outcome.notice)}else{aiNotice=null;recommendation=outcome.recommendation}
        aiBusy=false;return;
      }
      const preview=previewJobs.get(event.id);
      if(preview){previewJobs.delete(event.id);if(!projectSession.isCurrent(preview.context))return;if(typeof event.result.preview_path==='string'){if(event.result.synthetic_depth===true&&$appStore.project?.depthMode!=='image-analysis'){toast('warning','Preview used non-certified depth','This render used the built-in image-analysis estimator rather than the neural model.')}void resolveForProject(projectSession,preview.context,desktopBridge.assetUrl(event.result.preview_path),(url)=>{const next=new Set(previewRenderingShots);next.delete(preview.shotId);previewRenderingShots=next;if(url){previewUrls={...previewUrls,[preview.shotId]:url};if(preview.autoplay){appStore.setCurrentTime(($appStore.project?.shots.find((item)=>item.id===preview.shotId)?.startSeconds)??$appStore.currentTime);appStore.setPlaying(true)}}},(error)=>{const next=new Set(previewRenderingShots);next.delete(preview.shotId);previewRenderingShots=next;toast('error','Preview could not be displayed',errorMessage(error,'The rendered file could not be opened.'))})}else{const next=new Set(previewRenderingShots);next.delete(preview.shotId);previewRenderingShots=next;toast('error','Preview response was invalid','The engine did not return a rendered preview path.')}}
      if(!event.id.startsWith('llm-')&&!event.id.startsWith('recommend'))toast('success','Processing complete',String(event.result.output_path??event.result.preview_path??'Output is ready.'));
    }else if(event.type==='error'){
      const recovery=cachedStageRecoveryRequests.get(event.id);if(recovery){cachedStageRecoveryRequests.delete(event.id);return}
      if(event.id===pendingSave?.id){const active=pendingSave;active.savedIds.forEach((shotId)=>dirtyShotIds.add(shotId));active.resolve(false);pendingSave=null;if(event.error.code==='revision_conflict'){revisionConflict=true;toast('warning','Project changed on disk','Your local values are preserved and autosave is paused. Reopen the project when you are ready to reconcile the newer disk version.')}else{toast('error','Director changes were not saved',event.error.message)}return}
      const importStage=importStageForId(event.id);if(importStage)failCurrentImport(importStage,event.error.message,event.error.code==='cancelled'||event.error.code==='job_cancelled')
      const opening=openProjectRequest;if(event.id===opening?.id){openProjectRequest=null;if(projectSession.isCurrent(opening.context))appStore.setBusy(false)}
      const refresh=summaryRefresh;if(event.id===refresh?.id){summaryRefresh=null;if(projectSession.isCurrent(refresh.context))toast('info','Monitor refresh unavailable','Analysis is complete, but the normalized source monitor could not be refreshed.');return}
      if(event.id===testRequestId){testState='error';testRequestId=null}
      const assistantRequest=aiRequest;if(event.id===assistantRequest?.id){aiRequest=null;if(!projectSession.isCurrent(assistantRequest.context))return;aiBusy=false}
      const preview=previewJobs.get(event.id);if(preview){previewJobs.delete(event.id);const next=new Set(previewRenderingShots);next.delete(preview.shotId);previewRenderingShots=next;if(!projectSession.isCurrent(preview.context))return}
      toast('error',event.error.retryable?'Worker error · retry available':'Worker error',event.error.message);
    }
  }

  function handleKeydown(event:KeyboardEvent){
    const target=event.target as HTMLElement|null;
    const editing=target?.matches('input,textarea,select,[contenteditable="true"]');
    const command=event.ctrlKey||event.metaKey;
    if(event.key==='Escape'){if(newProjectSourcePath){cancelNewProjectSetup();return}settingsOpen=false;exportOpen=false;shortcutsOpen=false;return}
    if(newProjectSourcePath)return;
    if(command&&event.key.toLowerCase()==='s'){event.preventDefault();saveProject();return}
    if(command&&event.key.toLowerCase()==='e'){event.preventDefault();if($appStore.project&&workspaceCapabilities.finalExport.enabled)exportOpen=true;else if($appStore.project)toast('warning','Export is still locked',workspaceCapabilities.finalExport.reason);return}
    if(command&&event.key.toLowerCase()==='z'){event.preventDefault();applyHistory(event.shiftKey?'redo':'undo');return}
    if(command&&event.key.toLowerCase()==='y'){event.preventDefault();applyHistory('redo');return}
    if(editing)return;
    if(event.code==='Space'&&$appStore.project&&workspaceCapabilities.shotNavigation.enabled){event.preventDefault();togglePlayback()}
    else if(event.key==='ArrowLeft'&&$appStore.project){event.preventDefault();event.shiftKey?appStore.stepShot(-1):appStore.setCurrentTime($appStore.currentTime-1/$appStore.project.fps)}
    else if(event.key==='ArrowRight'&&$appStore.project){event.preventDefault();event.shiftKey?appStore.stepShot(1):appStore.setCurrentTime($appStore.currentTime+1/$appStore.project.fps)}
    else if(event.key==='1'&&originalAssetUrl)appStore.setViewMode('original');else if(event.key==='2'&&selectedShot&&(desktopBridge.mode==='browser-demo'||previewUrls[selectedShot.id]||workspaceCapabilities.previewRender.enabled)){appStore.setViewMode('anaglyph');if(desktopBridge.mode!=='browser-demo'&&!previewUrls[selectedShot.id])void renderPreview()}else if(event.key==='3'&&selectedShot&&originalAssetUrl&&(desktopBridge.mode==='browser-demo'||previewUrls[selectedShot.id]||workspaceCapabilities.previewRender.enabled)){appStore.setViewMode('split');if(desktopBridge.mode!=='browser-demo'&&!previewUrls[selectedShot.id])void renderPreview()}else if(event.key==='4'&&desktopBridge.mode==='browser-demo')appStore.setViewMode('depth');else if(event.key.toLowerCase()==='r'&&$appStore.project&&workspaceCapabilities.previewRender.enabled)renderPreview(event.shiftKey?'video':'still');else if(event.key==='?')shortcutsOpen=true;
  }

  onMount(()=>{
    let unlisten:(()=>void)|undefined;
    desktopBridge.subscribe(handleWorkerEvent).then((value)=>unlisten=value).catch((error)=>toast('error','Desktop bridge unavailable',errorMessage(error,'Worker events could not be subscribed to.')));
    const animate=(time:number)=>{if(lastFrame&&$appStore.playing)appStore.tick(Math.min(.1,(time-lastFrame)/1000));lastFrame=time;frame=requestAnimationFrame(animate)};
    let frame=requestAnimationFrame(animate);
    const handleBeforeUnload=(event:BeforeUnloadEvent)=>{if($appStore.project?.dirty||dirtyShotIds.size||pendingSave){event.preventDefault();event.returnValue=''}};
    window.addEventListener('keydown',handleKeydown);
    window.addEventListener('beforeunload',handleBeforeUnload);
    return()=>{unlisten?.();cancelAnimationFrame(frame);if(saveTimer)clearTimeout(saveTimer);window.removeEventListener('keydown',handleKeydown);window.removeEventListener('beforeunload',handleBeforeUnload)};
  });
</script>

<div class:reduce-motion={$appStore.settings.reduceMotion} class="app-shell">
  <div class="app-underlay" inert={Boolean(newProjectSourcePath)} aria-hidden={newProjectSourcePath?'true':undefined}>
  {#if $appStore.view==='welcome'}
    <WelcomeView recentProjects={desktopBridge.mode==='browser-demo'?$appStore.recentProjects:[]} busy={$appStore.busy} onImport={importVideo} onOpenProject={openProject} onOpenRecent={openRecent} onDemo={openDemo} onSettings={openSettings}/>
  {:else if $appStore.workspace || $appStore.project}
    <TitleBar project={$appStore.project} workspace={$appStore.workspace?.identity??null} preparing={workspacePreparing} productionActive={productionAnalysisActive} saveEnabled={desktopBridge.mode==='browser-demo'||(workspaceCapabilities.directorEdit.enabled&&!productionFinalizing)} exportEnabled={desktopBridge.mode==='browser-demo'||workspaceCapabilities.finalExport.enabled} shotsEnabled={workspaceCapabilities.shotNavigation.enabled} onHome={closeProjectSafely} onSave={saveProject} onSettings={openSettings} onExport={()=>{if(workspaceCapabilities.finalExport.enabled||desktopBridge.mode==='browser-demo')exportOpen=true}} onToggleShots={()=>shotsOpen=!shotsOpen}/>
    <div class="editor-stack">
      {#if $appStore.workspace}<AnalysisRail workspace={$appStore.workspace} onRetry={retryAnalysis} onStartProduction={startProductionAnalysis}/>{/if}
      {#if $appStore.project && selectedShot}
        <div class:shots-hidden={!shotsOpen} class="editor-workspace">
          {#if shotsOpen}<ShotList project={$appStore.project} demoMode={desktopBridge.mode==='browser-demo'} featuresReady={desktopBridge.mode==='browser-demo'||workspaceCapabilities.shotMetrics.enabled} scriptReady={desktopBridge.mode==='browser-demo'||workspaceCapabilities.directorEdit.enabled} productionActive={productionAnalysisActive} {previewedShotIds} {renderingShotIds} {renderAllBusy} renderAllEnabled={desktopBridge.mode==='tauri'&&workspaceCapabilities.previewRender.enabled} onRenderAll={renderAllShots} selectedShotId={$appStore.selectedShotId} selectedShotIds={$appStore.selectedShotIds} onSelect={(id,additive,range)=>appStore.selectShot(id,additive,range)}/>{/if}
          <PreviewStage project={$appStore.project} shot={selectedShot} demoMode={desktopBridge.mode==='browser-demo'} originalUrl={originalAssetUrl} previewUrl={previewUrls[selectedShot.id]??null} previewEnabled={desktopBridge.mode==='browser-demo'||workspaceCapabilities.previewRender.enabled} previewRendering={selectedPreviewRendering} previewLockedReason={workspaceCapabilities.previewRender.reason??'Full-frame analysis must finish before previews can be rendered.'} viewMode={$appStore.viewMode} currentTime={$appStore.currentTime} playing={$appStore.playing} showSafeZones={$appStore.settings.showSafeZones} onViewMode={(mode)=>appStore.setViewMode(mode)} onRequestPreview={renderPreview} onToggleSafeZones={()=>appStore.updateSettings({showSafeZones:!$appStore.settings.showSafeZones})} onTogglePlayback={togglePlayback} onPlaybackState={(playing)=>appStore.setPlaying(playing)} onStepShot={(direction)=>appStore.stepShot(direction)} onSeek={(seconds)=>appStore.setCurrentTime(seconds)}/>
          <DirectorPanel shot={selectedShot} {selectionCount} editingEnabled={desktopBridge.mode==='browser-demo'||(workspaceCapabilities.directorEdit.enabled&&!productionFinalizing)} featuresReady={desktopBridge.mode==='browser-demo'||workspaceCapabilities.shotMetrics.enabled} previewEnabled={desktopBridge.mode==='browser-demo'||workspaceCapabilities.previewRender.enabled} previewRendering={selectedPreviewRendering} analysisTier={$appStore.project.analysisTier} draftCoverage={$appStore.project.draftCoverage} draftStage={$appStore.workspace?.stages.draft} productionActive={productionAnalysisActive} {productionStage} {productionStageLabel} {productionFinalizing} canStartProduction={canStartProductionAnalysis} llmEnabled={$appStore.settings.llmEnabled&&hasLlmKey} {aiBusy} {recommendation} {aiNotice} onApplyPreset={editPreset} onParameter={editParameter} onPreview={renderPreview} onAskAi={askAi} onOpenSettings={openSettings} onStartProduction={startProductionAnalysis}/>
          <div class="timeline-slot"><Timeline project={$appStore.project} currentTime={$appStore.currentTime} {previewedShotIds} {renderingShotIds} selectedShotId={$appStore.selectedShotId} zoom={$appStore.zoom} onSeek={(seconds)=>appStore.setCurrentTime(seconds)} onSelect={(id,additive,range)=>appStore.selectShot(id,additive,range)} onZoom={(zoom)=>appStore.setZoom(zoom)}/></div>
        </div>
      {:else if $appStore.workspace}
        <PreparingWorkspace workspace={$appStore.workspace} capabilities={workspaceCapabilities} sourceUrl={originalAssetUrl}/>
      {/if}
    </div>
  {/if}

  <ToastStack {toasts} onDismiss={(id)=>toasts=toasts.filter((toast)=>toast.id!==id)}/>
  <ProcessingQueue queue={$appStore.queue} onCancel={cancelJob} onDismiss={(id)=>appStore.removeQueueItem(id)}/>

  {#if settingsOpen}<SettingsModal settings={$appStore.settings} {hasLlmKey} credentialStorageAvailable={desktopBridge.mode==='tauri'} {keyBusy} {testState} onUpdate={(settings)=>appStore.updateSettings(settings)} onClose={()=>settingsOpen=false} onSaveKey={saveLlmKey} onDeleteKey={deleteLlmKey} onTestLlm={testLlm}/>{/if}
  {#if exportOpen && $appStore.project && (desktopBridge.mode==='browser-demo'||workspaceCapabilities.finalExport.enabled)}<ExportModal project={$appStore.project} defaultMode={$appStore.settings.anaglyphMode} productionReady={desktopBridge.mode==='browser-demo'||workspaceCapabilities.finalExport.enabled} blockedReason={workspaceCapabilities.finalExport.reason} depthTier={$appStore.workspace?.artifacts.depthMode??$appStore.project.depthMode} onChooseOutput={(name)=>desktopBridge.saveOutput(name,'mp4')} onExport={startExport} onClose={()=>exportOpen=false}/>{/if}
  {#if shortcutsOpen}
    <div class="shortcut-backdrop" role="presentation" on:mousedown={(e)=>e.currentTarget===e.target&&(shortcutsOpen=false)}><div class="shortcut-modal" role="dialog" aria-modal="true" aria-labelledby="shortcut-title"><header><div><Keyboard size={16}/><h2 id="shortcut-title">Keyboard shortcuts</h2></div><button on:click={()=>shortcutsOpen=false}><X size={15}/></button></header><div class="shortcut-grid"><span>Play / pause</span><kbd>Space</kbd><span>Previous / next frame</span><div><kbd>←</kbd><kbd>→</kbd></div><span>Previous / next shot</span><div><kbd>Shift</kbd> + <kbd>←</kbd><kbd>→</kbd></div><span>Preview modes</span><div><kbd>1</kbd>–<kbd>4</kbd></div><span>Render preview frame</span><kbd>R</kbd><span>Render playable 3D clip</span><div><kbd>Shift</kbd> + <kbd>R</kbd></div><span>Save project</span><div><kbd>Ctrl</kbd> + <kbd>S</kbd></div><span>Export master</span><div><kbd>Ctrl</kbd> + <kbd>E</kbd></div><span>Undo / redo</span><div><kbd>Ctrl</kbd> + <kbd>Z</kbd>/<kbd>Y</kbd></div></div><footer><Command size={12}/> Press <kbd>?</kbd> anywhere to reopen this guide.</footer></div></div>
  {/if}
  </div>
  {#if newProjectSourcePath}<NewProjectSetup sourcePath={newProjectSourcePath} baseDirectory={newProjectBaseDirectory} defaultBaseDirectory={newProjectDefaultBase} usesDefault={newProjectUsesDefault} plan={newProjectPlan} busy={projectSetupBusy} allocating={projectSetupAllocating} error={projectSetupError} onChangeBase={changeNewProjectBase} onUseDefault={useDefaultProjectBase} onCreate={confirmNewProject} onCancel={cancelNewProjectSetup}/>{/if}
</div>
