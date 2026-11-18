import * as THREE from 'three';
import { ISpawnPoint } from '../interfaces/ISpawnPoint';
import { World } from '../world/World';
import { Vehicle } from '../vehicles/Vehicle';
import { Character } from '../characters/Character';
import { LoadingManager } from '../core/LoadingManager';
export declare class VehicleSpawnPoint implements ISpawnPoint {
    type: string;
    driver: string;
    firstAINode: string;
    onSpawned: (vehicle: Vehicle, driver?: Character) => void;
    private object;
    constructor(object: THREE.Object3D);
    spawn(loadingManager: LoadingManager, world: World): void;
    private getNewVehicleByType;
}
