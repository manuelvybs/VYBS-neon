import { World } from './World';
import { LoadingManager } from '../core/LoadingManager';
import { IUpdatable } from '../interfaces/IUpdatable';
/**
 * The opening game mode: you start at speed in the pink car on the oval while dark
 * chaser cars with flashing lights hunt you. Survive as long as you can; more chasers
 * join over time. Get pinned and you're caught, then it restarts.
 */
export declare class ChaseMode implements IUpdatable {
    updateOrder: number;
    private world;
    private player;
    private chasers;
    private state;
    private elapsed;
    private best;
    private caughtMeter;
    private nextReinforcement;
    private flashClock;
    private restartTimer;
    private generation;
    private tmpA;
    private tmpB;
    private hud;
    private timeEl;
    private subEl;
    private meterEl;
    private splashEl;
    private splashTimer;
    private static readonly PLAYER_NODE;
    private static readonly CHASER_NODES;
    private static readonly RESERVE_NODES;
    private static readonly TRAFFIC_NODES;
    private static readonly REINFORCE_EVERY;
    private static readonly START_SPEED;
    private static readonly BEST_KEY;
    constructor(world: World);
    isActive(): boolean;
    stop(): void;
    launch(loadingManager?: LoadingManager): void;
    update(timeStep: number): void;
    private begin;
    private caught;
    private activate;
    private findNode;
    private spawnCar;
    private spawnChaser;
    /** Dark body plus a red/blue neon light bar on the windscreen frame. */
    private dressAsChaser;
    private createHud;
    private showSplash;
    private static formatTime;
}
