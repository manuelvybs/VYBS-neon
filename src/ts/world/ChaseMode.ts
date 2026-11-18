import * as THREE from 'three';
import { World } from './World';
import { LoadingManager } from '../core/LoadingManager';
import { UIManager } from '../core/UIManager';
import { VehicleSpawnPoint } from './VehicleSpawnPoint';
import { PathNode } from './PathNode';
import { Vehicle } from '../vehicles/Vehicle';
import { Character } from '../characters/Character';
import { FollowTarget } from '../characters/character_ai/FollowTarget';
import { IUpdatable } from '../interfaces/IUpdatable';
import * as Utils from '../core/FunctionLibrary';

interface Chaser
{
	vehicle: Vehicle;
	driver: Character;
	red: THREE.Object3D[];
	blue: THREE.Object3D[];
	active: boolean;
}

/**
 * The opening game mode: you start at speed in the pink car on the oval while dark
 * chaser cars with flashing lights hunt you. Survive as long as you can; more chasers
 * join over time. Get pinned and you're caught, then it restarts.
 */
export class ChaseMode implements IUpdatable
{
	public updateOrder: number = 6;

	private world: World;
	private player: Character;
	private chasers: Chaser[] = [];
	private state: 'idle' | 'loading' | 'run' | 'caught' = 'idle';
	private elapsed: number = 0;
	private best: number = 0;
	private caughtMeter: number = 0;
	private nextReinforcement: number = 0;
	private flashClock: number = 0;
	private restartTimer: any;
	// Bumped on every launch; loads that finish for an older launch clean themselves up
	private generation: number = 0;
	private tmpA: THREE.Vector3 = new THREE.Vector3();
	private tmpB: THREE.Vector3 = new THREE.Vector3();

	private hud: HTMLElement;
	private timeEl: HTMLElement;
	private subEl: HTMLElement;
	private meterEl: HTMLElement;
	private splashEl: HTMLElement;
	private splashTimer: any;

	// Where things start, by path node name (path1 = the oval on the terrace, driven in node order)
	private static readonly PLAYER_NODE = 'path1_node21';
	private static readonly CHASER_NODES = ['path1_node20', 'path1_node0'];
	private static readonly RESERVE_NODES = ['path1_node26', 'path1_node5', 'path1_node15'];
	private static readonly TRAFFIC_NODES = ['path1_node12', 'path1_node17', 'path1_node7', 'path3_node3', 'path3_node4', 'path2_node1', 'path2_node16'];
	private static readonly REINFORCE_EVERY = 25;
	private static readonly START_SPEED = 14;
	private static readonly BEST_KEY = 'vybs-neon-best-chase';

	constructor(world: World)
	{
		this.world = world;
		try
		{
			this.best = parseFloat(window.localStorage.getItem(ChaseMode.BEST_KEY)) || 0;
		}
		catch (e)
		{
			this.best = 0;
		}
		this.createHud();
		world.registerUpdatable(this);
	}

	public isActive(): boolean
	{
		return this.state !== 'idle';
	}

	public stop(): void
	{
		this.state = 'idle';
		clearTimeout(this.restartTimer);
		clearTimeout(this.splashTimer);
		this.hud.style.display = 'none';
		this.splashEl.style.display = 'none';
		this.world.timeScaleTarget = 1;
	}

	public launch(loadingManager?: LoadingManager): void
	{
		this.stop();
		this.state = 'loading';
		const gen = ++this.generation;
		this.world.clearEntities();
		// Right after the world loads, parent transforms are not baked into matrixWorld yet,
		// and getWorldPosition only refreshes the node itself. Path nodes sit under a raised parent.
		this.world.graphicsWorld.updateMatrixWorld(true);
		this.chasers = [];
		this.player = undefined;

		const lm = loadingManager || new LoadingManager(this.world);
		lm.onFinishedCallback = () =>
		{
			if (gen !== this.generation) return;
			this.world.update(1, 1);
			this.world.setTimeScale(1);
			UIManager.setUserInterfaceVisible(true);
			this.begin();
		};

		this.spawnCar(lm, ChaseMode.PLAYER_NODE, 'player', undefined, (vehicle, driver) =>
		{
			this.player = driver;
		});
		ChaseMode.CHASER_NODES.forEach((node) => this.spawnChaser(lm, node, true));
		ChaseMode.RESERVE_NODES.forEach((node) => this.spawnChaser(lm, node, false));
		ChaseMode.TRAFFIC_NODES.forEach((nodeName) =>
		{
			const node = this.findNode(nodeName);
			if (node !== undefined) this.spawnCar(lm, nodeName, 'ai', node.nextNode.object.name);
		});
	}

	public update(timeStep: number): void
	{
		if (this.state !== 'run' && this.state !== 'caught') return;

		// Flash the light bars
		this.flashClock += timeStep;
		const phase = Math.floor(this.flashClock / 0.22) % 2 === 0;
		this.chasers.forEach((c) =>
		{
			const on = c.active;
			c.red.forEach((o) => o.visible = on && phase);
			c.blue.forEach((o) => o.visible = on && !phase);
		});

		if (this.state !== 'run' || this.player === undefined) return;

		this.elapsed += timeStep;
		this.timeEl.textContent = ChaseMode.formatTime(this.elapsed);

		// Reinforcements
		if (this.elapsed >= this.nextReinforcement)
		{
			const reserve = this.chasers.find((c) => !c.active);
			if (reserve !== undefined)
			{
				this.activate(reserve);
				this.showSplash('+1 CHASER', 1.2);
			}
			this.nextReinforcement += ChaseMode.REINFORCE_EVERY;
		}

		// Caught: a chaser stays right on top of you while you're (nearly) stopped
		const playerPos = this.player.getWorldPosition(this.tmpA);
		const vehicle = this.player.occupyingSeat !== null && this.player.occupyingSeat !== undefined ?
			this.player.occupyingSeat.vehicle as unknown as Vehicle : undefined;
		const speed = vehicle !== undefined ? vehicle.collision.velocity.length() : this.player.velocity.length() * 4;

		let nearest = Infinity;
		const p = this.tmpB;
		this.chasers.forEach((c) =>
		{
			if (!c.active) return;
			c.vehicle.getWorldPosition(p);
			nearest = Math.min(nearest, p.distanceTo(playerPos));
		});

		const pinned = nearest < (vehicle !== undefined ? 6.0 : 3.5) && speed < 6;
		this.caughtMeter = pinned ? this.caughtMeter + timeStep : Math.max(0, this.caughtMeter - timeStep * 0.35);
		const danger = Math.min(1, this.caughtMeter / 1.6);
		this.meterEl.style.width = (danger * 100).toFixed(0) + '%';
		this.meterEl.parentElement.style.opacity = danger > 0 ? '1' : '0';

		const active = this.chasers.filter((c) => c.active).length;
		this.subEl.textContent = active + ' CHASERS ON YOU  |  BEST ' + ChaseMode.formatTime(this.best);

		if (this.caughtMeter >= 1.6) this.caught();
	}

	private begin(): void
	{
		this.state = 'run';
		this.elapsed = 0;
		this.caughtMeter = 0;
		this.flashClock = 0;
		this.nextReinforcement = ChaseMode.REINFORCE_EVERY;
		this.chasers.forEach((c) => { if (c.active) this.activate(c); });

		// Start already moving, with the camera low behind the car
		const vehicle = this.player !== undefined && this.player.occupyingSeat ? this.player.occupyingSeat.vehicle as unknown as Vehicle : undefined;
		if (vehicle !== undefined)
		{
			const fwd = new THREE.Vector3(0, 0, 1).applyQuaternion(vehicle.quaternion);
			vehicle.collision.velocity.set(fwd.x * ChaseMode.START_SPEED, 0, fwd.z * ChaseMode.START_SPEED);
			this.world.cameraOperator.theta = Math.atan2(-fwd.x, -fwd.z) * 180 / Math.PI;
			this.world.cameraOperator.phi = 12;
		}
		this.chasers.forEach((c) =>
		{
			if (!c.active) return;
			const fwd = new THREE.Vector3(0, 0, 1).applyQuaternion(c.vehicle.quaternion);
			c.vehicle.collision.velocity.set(fwd.x * ChaseMode.START_SPEED, 0, fwd.z * ChaseMode.START_SPEED);
		});

		this.hud.style.display = 'block';
		this.timeEl.textContent = ChaseMode.formatTime(0);
		this.showSplash('ESCAPE!', 1.4);
	}

	private caught(): void
	{
		this.state = 'caught';
		const lasted = this.elapsed;
		const record = lasted > this.best;
		if (record)
		{
			this.best = lasted;
			try
			{
				window.localStorage.setItem(ChaseMode.BEST_KEY, String(this.best));
			}
			catch (e)
			{
				// Storage can be blocked; the best time just won't persist
			}
		}
		this.world.timeScaleTarget = 0.25;
		this.showSplash('CAUGHT!', 3, (record ? 'NEW BEST ' : 'YOU LASTED ') + ChaseMode.formatTime(lasted));
		this.restartTimer = setTimeout(() =>
		{
			this.world.timeScaleTarget = 1;
			if (this.state === 'caught') this.launch();
		}, 2600);
	}

	private activate(c: Chaser): void
	{
		c.active = true;
		if (this.player !== undefined && c.driver !== undefined)
		{
			c.driver.setBehaviour(new FollowTarget(this.player, 0.5));
		}
	}

	// ---------- spawning ----------

	private findNode(name: string): PathNode
	{
		for (const path of this.world.paths)
		{
			if (path.nodes.hasOwnProperty(name)) return path.nodes[name];
		}
		console.warn('Chase: path node ' + name + ' not found');
		return undefined;
	}

	private spawnCar(lm: LoadingManager, nodeName: string, driver: string, firstAINode?: string,
		onSpawned?: (vehicle: Vehicle, driver: Character) => void): void
	{
		const node = this.findNode(nodeName);
		if (node === undefined) return;

		// A spawn marker on the node, facing along the route
		const marker = new THREE.Object3D();
		node.object.getWorldPosition(marker.position);
		const aim = new THREE.Vector3();
		node.nextNode.object.getWorldPosition(aim);
		aim.y = marker.position.y;
		marker.lookAt(aim);
		marker.updateMatrixWorld(true);

		const sp = new VehicleSpawnPoint(marker);
		sp.type = 'car';
		sp.driver = driver;
		sp.firstAINode = firstAINode;
		const gen = this.generation;
		sp.onSpawned = (vehicle: Vehicle, driverChar?: Character) =>
		{
			// Finished loading after a newer launch started: take it straight back out
			if (gen !== this.generation)
			{
				if (driverChar !== undefined && this.world.characters.indexOf(driverChar) !== -1) this.world.remove(driverChar);
				if (this.world.vehicles.indexOf(vehicle) !== -1) this.world.remove(vehicle);
				return;
			}
			if (onSpawned !== undefined) onSpawned(vehicle, driverChar);
		};
		sp.spawn(lm, this.world);
	}

	private spawnChaser(lm: LoadingManager, nodeName: string, active: boolean): void
	{
		this.spawnCar(lm, nodeName, 'ai', undefined, (vehicle, driver) =>
		{
			const c: Chaser = { vehicle: vehicle, driver: driver, red: [], blue: [], active: active };
			this.dressAsChaser(c);
			this.chasers.push(c);
			if (this.state === 'run' && active) this.activate(c);
		});
	}

	/** Dark body plus a red/blue neon light bar on the windscreen frame. */
	private dressAsChaser(c: Chaser): void
	{
		// Flat near-black paint on the car itself (not on the astronaut driving it); neon trim stays lit
		const insideDriver = (o: THREE.Object3D): boolean =>
		{
			for (let p = o; p !== null && p !== undefined; p = p.parent) if (p === c.driver) return true;
			return false;
		};
		c.vehicle.traverse((child: any) =>
		{
			if (child.isMesh && child.material !== undefined && child.material.name === 'retro' && !insideDriver(child))
			{
				const dark = child.material.clone();
				dark.map = null;
				dark.color.setHex(0x221f36).convertSRGBToLinear();
				if (Utils.isLitMaterial(dark)) this.world.sky.csm.setupMaterial(dark);
				child.material = dark;
			}
		});

		const bar = new THREE.Group();
		const mk = (color: number, x: number, list: THREE.Object3D[]) =>
		{
			const lamp = new THREE.Mesh(new THREE.BoxBufferGeometry(0.34, 0.09, 0.14), new THREE.MeshBasicMaterial({ color: color, fog: false }));
			const halo = new THREE.Mesh(new THREE.BoxBufferGeometry(0.52, 0.24, 0.3), new THREE.MeshBasicMaterial({
				color: color, transparent: true, opacity: 0.45, blending: THREE.AdditiveBlending, depthWrite: false, fog: false }));
			lamp.position.x = halo.position.x = x;
			halo.renderOrder = 1;
			bar.add(lamp, halo);
			list.push(lamp, halo);
		};
		mk(0xff2244, 0.2, c.red);
		mk(0x2266ff, -0.2, c.blue);
		bar.add(new THREE.Mesh(new THREE.BoxBufferGeometry(0.8, 0.05, 0.16), new THREE.MeshBasicMaterial({ color: 0x120818 })));
		bar.position.set(0, 0.78, 0.05);
		c.vehicle.add(bar);
	}

	// ---------- HUD ----------

	private createHud(): void
	{
		const style = document.createElement('style');
		style.textContent = `
			#chase-hud { position: fixed; top: 14px; left: 50%; transform: translateX(-50%); text-align: center;
				pointer-events: none; display: none; font-family: 'Alfa Slab One', sans-serif; z-index: 2; }
			#chase-hud .chase-time { font-size: 44px; color: #FFE45C; letter-spacing: 2px;
				text-shadow: 0 0 6px #FF2E97, 0 0 18px #FF2E97, 3px 3px 0 #1B0B33; }
			#chase-hud .chase-sub { font-family: 'Solway', sans-serif; font-weight: 700; font-size: 14px; color: #19E3FF;
				letter-spacing: 2px; text-shadow: 0 0 6px #19E3FF, 2px 2px 0 #1B0B33; margin-top: 2px; }
			#chase-hud .chase-meter { width: 220px; height: 8px; margin: 8px auto 0; border: 2px solid #FF2E97;
				background: rgba(27, 11, 51, 0.6); opacity: 0; transition: opacity 0.2s; }
			#chase-hud .chase-meter div { height: 100%; width: 0; background: #FF2244; box-shadow: 0 0 10px #FF2244; }
			#chase-splash { position: fixed; top: 38%; left: 50%; transform: translate(-50%, -50%); text-align: center;
				pointer-events: none; display: none; font-family: 'Alfa Slab One', sans-serif; z-index: 2; }
			#chase-splash .big { font-size: 84px; color: #FF2E97; letter-spacing: 4px;
				text-shadow: 0 0 10px #FF2E97, 0 0 30px #FF2E97, 5px 5px 0 #1B0B33; }
			#chase-splash .small { font-family: 'Solway', sans-serif; font-weight: 700; font-size: 22px; color: #FFE45C;
				letter-spacing: 3px; text-shadow: 0 0 8px #FF7B3A, 3px 3px 0 #1B0B33; }
		`;
		document.head.appendChild(style);

		this.hud = document.createElement('div');
		this.hud.id = 'chase-hud';
		this.hud.innerHTML = '<div class="chase-time">00:00.0</div><div class="chase-sub"></div><div class="chase-meter"><div></div></div>';
		document.body.appendChild(this.hud);
		this.timeEl = this.hud.querySelector('.chase-time');
		this.subEl = this.hud.querySelector('.chase-sub');
		this.meterEl = this.hud.querySelector('.chase-meter div');

		this.splashEl = document.createElement('div');
		this.splashEl.id = 'chase-splash';
		this.splashEl.innerHTML = '<div class="big"></div><div class="small"></div>';
		document.body.appendChild(this.splashEl);
	}

	private showSplash(big: string, seconds: number, small: string = ''): void
	{
		(this.splashEl.querySelector('.big') as HTMLElement).textContent = big;
		(this.splashEl.querySelector('.small') as HTMLElement).textContent = small;
		this.splashEl.style.display = 'block';
		clearTimeout(this.splashTimer);
		this.splashTimer = setTimeout(() => this.splashEl.style.display = 'none', seconds * 1000);
	}

	private static formatTime(t: number): string
	{
		const m = Math.floor(t / 60);
		const s = t - m * 60;
		return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s.toFixed(1);
	}
}
